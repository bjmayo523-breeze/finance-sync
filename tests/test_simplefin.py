import base64
from datetime import datetime, timedelta, timezone

import pytest
from conftest import FakeResponse, FakeSession

from finance_sync.models import FetchResult
from finance_sync.providers import simplefin as sf

ACCESS = "https://user123:secretpw@bridge.simplefin.org/simplefin"


def token_for(url: str) -> str:
    return base64.b64encode(url.encode()).decode()


def test_decode_setup_token_roundtrip():
    url = "https://bridge.simplefin.org/simplefin/claim/abc"
    assert sf.decode_setup_token(token_for(url)) == url
    # tolerate whitespace / missing padding
    assert sf.decode_setup_token("  " + token_for(url).rstrip("=") + "\n") == url


def test_decode_rejects_non_https():
    with pytest.raises(sf.SimpleFINError):
        sf.decode_setup_token(token_for("http://bridge.simplefin.org/claim/x"))
    with pytest.raises(sf.SimpleFINError):
        sf.decode_setup_token("")


def test_claim_success_and_403():
    s = FakeSession(post=lambda url, **kw: FakeResponse(200, text=ACCESS + "\n"))
    assert sf.claim(token_for("https://bridge.simplefin.org/claim/x"), session=s) == ACCESS
    s403 = FakeSession(post=lambda url, **kw: FakeResponse(403))
    with pytest.raises(sf.SimpleFINError, match="already"):
        sf.claim(token_for("https://bridge.simplefin.org/claim/x"), session=s403)


def test_claim_rejects_response_without_credentials():
    s = FakeSession(post=lambda url, **kw: FakeResponse(200, text="https://bridge.simplefin.org/x"))
    with pytest.raises(sf.SimpleFINError):
        sf.claim(token_for("https://bridge.simplefin.org/claim/x"), session=s)


def test_redact():
    assert "secretpw" not in sf.redact(ACCESS)
    assert "user123" not in sf.redact(ACCESS)


def test_parse(payload):
    p = sf.SimpleFINProvider(ACCESS, tz="America/Chicago")
    r = FetchResult()
    p.parse(payload, r, include_pending=False)
    assert len(r.accounts) == 3
    assert r.accounts["ACT-broker-003"].institution == "Example Brokerage"  # from connections
    assert r.accounts["ACT-checking-001"].institution == "Example Bank"
    descs = [t.description for t in r.transactions]
    assert "PENDING COFFEE" not in descs
    assert len(r.transactions) == 4 + 5 + 2
    # same provider txn id in two accounts -> different keys
    keys = {t.key for t in r.transactions}
    assert len(keys) == len(r.transactions)
    assert r.warnings == ["Connection to Example Credit Union may need attention"]
    assert {h.symbol for h in r.holdings} == {"VTI", "VXUS"}
    assert str(r.accounts["ACT-card-002"].balance) == "-1320.10"


def test_parse_with_pending(payload):
    p = sf.SimpleFINProvider(ACCESS)
    r = FetchResult()
    p.parse(payload, r, include_pending=True)
    # pending txn has posted=0 and no transacted_at -> no date -> still skipped
    assert "PENDING COFFEE" not in [t.description for t in r.transactions]


def test_fetch_chunks_and_auth(payload):
    s = FakeSession(get=lambda url, **kw: FakeResponse(200, body=payload))
    p = sf.SimpleFINProvider(ACCESS, chunk_days=60, session=s)
    end = datetime(2026, 10, 23, tzinfo=timezone.utc)
    r = p.fetch(end - timedelta(days=90), end)
    assert len(s.calls) == 2  # 90 days in 60-day chunks
    method, url, kw = s.calls[0]
    assert url == "https://bridge.simplefin.org/simplefin/accounts"
    assert "secretpw" not in url
    assert kw["auth"] == ("user123", "secretpw")
    assert kw["params"]["end-date"] > kw["params"]["start-date"]
    # identical payload in both windows -> deduped
    assert len(r.transactions) == 11
    assert len(r.warnings) == 1


def test_fetch_403_message():
    s = FakeSession(get=lambda url, **kw: FakeResponse(403))
    p = sf.SimpleFINProvider(ACCESS, session=s)
    now = datetime.now(timezone.utc)
    with pytest.raises(sf.SimpleFINError, match="revoked"):
        p.fetch(now - timedelta(days=1), now)
