"""SimpleFIN client (https://www.simplefin.org/protocol.html).

The protocol is read-only by design: the access URL can list accounts,
balances, transactions (and holdings where the institution provides them).
There is no endpoint that moves money.

Flow:
  1. You create a one-time *setup token* in the SimpleFIN Bridge web UI.
  2. `claim()` POSTs to the URL inside it and receives the long-lived
     *access URL* (https://user:pass@host/path). The token is then spent.
  3. `fetch()` calls GET {access URL}/accounts with HTTP Basic auth.

The access URL is the credential. It is never logged; it is stored only in the
encrypted vault.
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests

from finance_sync.models import Account, FetchResult, Holding, Transaction

log = logging.getLogger(__name__)

PROVIDER = "simplefin"


class SimpleFINError(RuntimeError):
    pass


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _ts(v) -> datetime | None:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return datetime.fromtimestamp(v, tz=timezone.utc)


def decode_setup_token(token: str) -> str:
    token = "".join(token.split())
    if not token:
        raise SimpleFINError("Setup token is empty.")
    padded = token + "=" * (-len(token) % 4)
    try:
        url = base64.b64decode(padded, validate=False).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError) as e:
        raise SimpleFINError("That is not a valid SimpleFIN setup token.") from e
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise SimpleFINError("Setup token does not contain an https claim URL.")
    return url


def claim(setup_token: str, session: requests.Session | None = None, timeout: int = 30) -> str:
    """Exchange a one-time setup token for the long-lived access URL."""
    claim_url = decode_setup_token(setup_token)
    host = urlsplit(claim_url).hostname or ""
    if not host.endswith("simplefin.org"):
        log.warning("Claim URL host is %s (not simplefin.org).", host)
    s = session or requests.Session()
    resp = s.post(claim_url, data=b"", headers={"Content-Length": "0"}, timeout=timeout)
    if resp.status_code == 403:
        raise SimpleFINError(
            "SimpleFIN refused the token (403). Tokens work once: it was already "
            "claimed or has expired. Generate a new one in the Bridge, and if you "
            "didn't claim it yourself, revoke the old connection there."
        )
    if resp.status_code != 200:
        raise SimpleFINError(f"Claim failed: HTTP {resp.status_code}.")
    access_url = resp.text.strip()
    p = urlsplit(access_url)
    if p.scheme != "https" or not p.username or not p.password:
        raise SimpleFINError("Claim returned an unexpected response (no credentials in URL).")
    return access_url


def redact(access_url: str) -> str:
    p = urlsplit(access_url)
    return urlunsplit((p.scheme, f"***@{p.hostname}", p.path, "", ""))


class SimpleFINProvider:
    name = PROVIDER

    def __init__(
        self,
        access_url: str,
        tz: str = "America/Chicago",
        timeout: int = 60,
        chunk_days: int = 60,
        session: requests.Session | None = None,
    ) -> None:
        p = urlsplit(access_url)
        if p.scheme != "https" or not p.username:
            raise SimpleFINError("Stored SimpleFIN access URL is malformed. Re-run setup-simplefin.")
        netloc = p.hostname + (f":{p.port}" if p.port else "")
        self._base = urlunsplit((p.scheme, netloc, p.path.rstrip("/"), "", ""))
        self._auth = (p.username, p.password or "")
        self._tz = ZoneInfo(tz)
        self._timeout = timeout
        self._chunk = max(1, int(chunk_days))
        self._s = session or requests.Session()

    # ---- HTTP -----------------------------------------------------------
    def _get_accounts(self, params: dict) -> dict:
        try:
            resp = self._s.get(
                f"{self._base}/accounts", params=params, auth=self._auth, timeout=self._timeout
            )
        except requests.RequestException as e:
            raise SimpleFINError(f"Could not reach SimpleFIN: {type(e).__name__}") from e
        if resp.status_code == 403:
            raise SimpleFINError(
                "SimpleFIN rejected the access credentials (403). The connection was "
                "revoked or disabled in the Bridge. Re-run setup-simplefin with a new token."
            )
        if resp.status_code == 402:
            raise SimpleFINError("SimpleFIN says payment is required (402). Check your Bridge subscription.")
        if resp.status_code != 200:
            raise SimpleFINError(f"SimpleFIN returned HTTP {resp.status_code}.")
        try:
            return resp.json()
        except ValueError as e:
            raise SimpleFINError("SimpleFIN returned non-JSON data.") from e

    # ---- Parsing --------------------------------------------------------
    @staticmethod
    def _warnings(payload: dict) -> list[str]:
        out: list[str] = []
        for e in payload.get("errors") or []:
            out.append(str(e))
        for e in payload.get("errlist") or []:  # newer Bridge format
            if isinstance(e, dict):
                out.append(str(e.get("msg") or e.get("message") or e))
            else:
                out.append(str(e))
        return out

    def _local_date(self, dt: datetime) -> date:
        return dt.astimezone(self._tz).date()

    def parse(self, payload: dict, result: FetchResult, include_pending: bool) -> None:
        conns = {}
        for c in payload.get("connections") or []:
            if isinstance(c, dict) and c.get("conn_id"):
                conns[c["conn_id"]] = c
        result.warnings.extend(self._warnings(payload))

        for a in payload.get("accounts") or []:
            acct_id = str(a.get("id") or "")
            if not acct_id:
                continue
            org = a.get("org") or {}
            inst = org.get("name") or org.get("domain") or ""
            if not inst and a.get("conn_id") in conns:
                inst = conns[a["conn_id"]].get("name") or ""
            result.accounts[acct_id] = Account(
                id=acct_id,
                name=str(a.get("name") or acct_id),
                institution=str(inst),
                currency=str(a.get("currency") or "USD"),
                balance=_dec(a.get("balance")),
                available=_dec(a.get("available-balance")),
                balance_date=_ts(a.get("balance-date")),
            )

            for t in a.get("transactions") or []:
                tid = str(t.get("id") or "")
                amount = _dec(t.get("amount"))
                if not tid or amount is None:
                    continue
                posted = _ts(t.get("posted"))
                pending = bool(t.get("pending")) or posted is None
                if pending and not include_pending:
                    continue
                when = _ts(t.get("transacted_at")) or posted
                if when is None:
                    continue
                result.transactions.append(
                    Transaction(
                        provider=PROVIDER,
                        account_id=acct_id,
                        provider_txn_id=tid,
                        date=self._local_date(when),
                        amount=amount,
                        description=" ".join(str(t.get("description") or "").split()),
                        payee=" ".join(str(t.get("payee") or "").split()),
                        memo=" ".join(str(t.get("memo") or "").split()),
                        pending=pending,
                        posted=posted,
                    )
                )

            for h in a.get("holdings") or []:
                if not isinstance(h, dict):
                    continue
                result.holdings.append(
                    Holding(
                        account_id=acct_id,
                        holding_id=str(h.get("id") or h.get("symbol") or h.get("description") or ""),
                        symbol=str(h.get("symbol") or ""),
                        description=str(h.get("description") or ""),
                        shares=_dec(h.get("shares")),
                        market_value=_dec(h.get("market_value")),
                        cost_basis=_dec(h.get("cost_basis")),
                        currency=str(h.get("currency") or a.get("currency") or "USD"),
                    )
                )

    # ---- Public ---------------------------------------------------------
    def fetch(self, start: datetime, end: datetime, include_pending: bool = False) -> FetchResult:
        """Fetch [start, end] in chunk_days windows. Latest window wins for balances/holdings."""
        result = FetchResult()
        windows: list[tuple[datetime, datetime]] = []
        cur = start
        while cur < end:
            nxt = min(cur + timedelta(days=self._chunk), end)
            windows.append((cur, nxt))
            cur = nxt
        if not windows:
            windows = [(start, end)]

        seen: set[str] = set()
        for i, (ws, we) in enumerate(windows):
            params = {"start-date": int(ws.timestamp()), "end-date": int(we.timestamp())}
            if include_pending:
                params["pending"] = 1
            log.info("SimpleFIN request %d/%d", i + 1, len(windows))
            payload = self._get_accounts(params)
            chunk = FetchResult()
            self.parse(payload, chunk, include_pending)
            result.accounts.update(chunk.accounts)  # later windows overwrite balances
            for t in chunk.transactions:
                if t.key not in seen:
                    seen.add(t.key)
                    result.transactions.append(t)
            if i == len(windows) - 1:
                result.holdings = chunk.holdings
            for w in chunk.warnings:
                if w not in result.warnings:
                    result.warnings.append(w)
        return result
