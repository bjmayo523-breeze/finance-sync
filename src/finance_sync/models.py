from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class Account:
    id: str
    name: str
    institution: str
    currency: str = "USD"
    balance: Decimal | None = None
    available: Decimal | None = None
    balance_date: datetime | None = None


@dataclass
class Transaction:
    provider: str
    account_id: str
    provider_txn_id: str
    date: date
    amount: Decimal  # negative = money out
    description: str
    payee: str = ""
    memo: str = ""
    pending: bool = False
    posted: datetime | None = None

    @property
    def key(self) -> str:
        """Stable, opaque ID used for dedupe in the sheet."""
        raw = f"{self.provider}|{self.account_id}|{self.provider_txn_id}".encode()
        return f"{self.provider[:2]}_{hashlib.sha1(raw).hexdigest()[:16]}"


@dataclass
class Holding:
    account_id: str
    holding_id: str
    symbol: str = ""
    description: str = ""
    shares: Decimal | None = None
    market_value: Decimal | None = None
    cost_basis: Decimal | None = None
    currency: str = "USD"


@dataclass
class FetchResult:
    accounts: dict[str, Account] = field(default_factory=dict)
    transactions: list[Transaction] = field(default_factory=list)
    holdings: list[Holding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
