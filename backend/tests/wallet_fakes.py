# Author: @SaadZarook
"""Fake connectors serving the planted wallet cases (dev/seed/PLANTED.md) for tests."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from probeowl.connectors.lookup import LookupResult
from probeowl.engine.locations import Connector
from probeowl.model import Entity, Probe
from probeowl.model.load import load_file

WALLET = Path(__file__).resolve().parents[2] / "examples" / "wallet"

# The planted cases: dev/seed/PLANTED.md, with the IDs and amounts of dev/seed/sqlserver/wallet.sql.
TOPUPS: dict[str, dict[str, Any]] = {
    "tp_91f2c7": {
        "amount": Decimal("500.00"),
        "pay": "pay_7hq2m9",
        "txn": "lt_55e1a0",
        "webhooks": 2,
        "ledger": 0,
    },
    "tp_91f2b9": {
        "amount": Decimal("200.00"),
        "pay": "pay_4kd8x1",
        "txn": "lt_55e19f",
        "webhooks": 1,
        "ledger": 2,
    },
    "tp_93b001": {
        "amount": Decimal("90.00"),
        "pay": "pay_3nw6t2",
        "txn": None,
        "webhooks": 1,
        "ledger": 0,
    },
}


def load(path: Path) -> Any:
    [document] = load_file(path)
    return document


ENTITY: Entity = load(WALLET / "entities" / "topup.yaml")
PROBE: Probe = load(WALLET / "probes" / "topup_completed.yaml")


class Fake:
    """A connector that answers from a function and records what it was asked."""

    def __init__(self, answer: Callable[[str, Mapping[str, object]], list[dict[str, Any]]]):
        self.answer = answer
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def lookup(
        self, lookup: str, parameters: Mapping[str, object], max_rows: int, /
    ) -> LookupResult:
        self.calls.append((lookup, dict(parameters)))
        rows = self.answer(lookup, parameters)
        truncated = f"more than {max_rows} rows" if len(rows) > max_rows else None
        return LookupResult(
            query=lookup,
            parameters={k: v for k, v in parameters.items() if isinstance(v, str)},
            guards=["fake guard"],
            rows=rows[:max_rows],
            truncated=truncated,
            started_at=datetime(2026, 10, 31, 9, 0, tzinfo=UTC),
            duration_ms=3,
        )

    def close(self) -> None:
        self.closed = True


def record(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
    # By the root key, or by another identifier when one is being resolved.
    if "topup_id" in p:
        found = [str(p["topup_id"])] if str(p["topup_id"]) in TOPUPS else []
    else:
        [(identifier, value)] = p.items()
        column = {"provider_payment_id": "pay", "ledger_txn_id": "txn"}[identifier]
        found = [topup_id for topup_id, t in TOPUPS.items() if t[column] == value]
    return [_record_row(topup_id, TOPUPS[topup_id]) for topup_id in found]


def _record_row(topup_id: str, t: dict[str, Any]) -> dict[str, Any]:
    return {
        "topup_id": topup_id,
        "user_id": "u_1",
        "amount": t["amount"],
        "currency": "USD",
        "status": t.get("status", "succeeded"),
        "provider_payment_id": t["pay"],
        "ledger_txn_id": t["txn"],
        "payment_method": "apple_pay",
        "app_version": "5.3.0",
        "customer_email": "c.customer@example.com",
        "created_at": "2026-10-31T08:00:00",
    }


def payment(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
    for topup_id, t in TOPUPS.items():
        if p.get("provider_payment_id") == t["pay"] or (
            "reference" in lookup and p.get("topup_id") == topup_id
        ):
            # A REST answer: JSON numbers arrive as text.
            return [
                {
                    "id": t["pay"],
                    "status": t.get("payment_status", "captured"),
                    "amount": str(t["amount"]),
                    "currency": "USD",
                    "customer": {"email": "c.customer@example.com"},
                }
            ]
    return []


def webhook(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
    t = next(t for t in TOPUPS.values() if t["pay"] == p["provider_payment_id"])
    return [
        {"id": i, "payment_id": t["pay"], "event_type": "captured"} for i in range(t["webhooks"])
    ]


def ledger(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
    t = next(t for t in TOPUPS.values() if t["txn"] == p["ledger_txn_id"])
    return [
        {"account": "wallet:u_1", "direction": d, "amount": t["amount"]}
        for d in ("credit", "debit")[: t["ledger"]]
    ]


def connectors(**changes: Connector | None) -> dict[str, Connector | None]:
    """wallet_db answers record and webhook lookups; the others have one location each."""

    def wallet_db(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        return webhook(lookup, p) if "provider_webhooks" in lookup else record(lookup, p)

    found: dict[str, Connector | None] = {
        "wallet_db": Fake(wallet_db),
        "checkout_api": Fake(payment),
        "ledger_db": Fake(ledger),
    }
    return found | changes
