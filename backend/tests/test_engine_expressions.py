# Author: @SaadZarook
"""The expression language of expectations and conditions, and masking."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from probeowl.engine.expressions import (
    Unavailable,
    compare,
    duplicates,
    evaluate,
    locations_used,
    parse,
)
from probeowl.engine.masking import MASKED, mask_rows, to_json
from probeowl.errors import ConfigError

LEDGER = [
    {"account": "wallet:u_1", "direction": "credit", "amount": Decimal("500.10")},
    {"account": "clearing", "direction": "debit", "amount": Decimal("500.10")},
]


class Data:
    def __init__(self, **rows: list[dict[str, Any]] | str):
        self._rows = rows

    def rows(self, location: str, *, counting: bool = False) -> list[dict[str, Any]]:
        value = self._rows[location]
        if isinstance(value, str):
            raise Unavailable(location, value)
        if not value and not counting:
            # As the runner does: no rows is Missing, which only counting may read.
            raise Unavailable(location, "Missing")
        return value

    def fanned_out(self, location: str, row: dict[str, Any]) -> list[dict[str, Any]] | None:
        # A fan-out location's rows ride on its parent's row in these tests.
        value = row.get(f"@{location}")
        return None if value is None else list(value)


DATA = Data(
    record=[
        {
            "status": "succeeded",
            "amount": Decimal("500.10"),
            "ledger_txn_id": None,
            "created_at": datetime(2026, 10, 31, 9, 0, tzinfo=UTC),
        }
    ],
    payment=[{"status": "captured", "amount": "500.10", "customer": {"email": "a@example.com"}}],
    ledger=LEDGER,
    webhook=[{"id": 1}, {"id": 2}],
    missing=[],
    blocked="Blocked (missing link: record.ledger_txn_id is empty)",
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("record.status in ['succeeded', 'failed', 'pending']", True),
        ("record.status not in ['failed']", True),
        ("payment.amount == record.amount", True),  # '500.10' and Decimal 500.10
        ("count(webhook) >= 1", True),
        ("count(ledger) == 2", True),
        (
            "sum(ledger.amount where direction == 'credit') "
            "== sum(ledger.amount where direction == 'debit')",
            True,
        ),
        ("not exists(missing)", True),
        ("exists(ledger) and count(missing) == 0", True),
        ("count(missing) == 1 or exists(webhook)", True),
        ("payment.status != 'captured'", False),
        ("record.ledger_txn_id == null", True),
        ("min(ledger.amount) == 500.1 and max(ledger.amount) == 500.10", True),
        ("all(ledger, amount > 0)", True),
        ("any(ledger where direction == 'debit', account == 'clearing')", True),
        ("none(ledger, amount < 0)", True),
        ("count(ledger where direction == 'refund') == 0", True),
        ("record.amount - payment.amount == 0", True),
        ("record.amount * 2 == 1000.2", True),
        ("payment.customer.email == 'a@example.com'", True),
        ("record.created_at < now()", True),
        ("-1 < 0", True),
        ('record.status == "succeeded"', True),
        ("'it''s' == 'it''s'", True),
    ],
)
def test_expressions_evaluate_by_meaning(text: str, expected: bool) -> None:
    assert evaluate(parse(text), DATA) is expected


def test_an_expression_that_needs_an_unreadable_location_raises_unavailable() -> None:
    with pytest.raises(Unavailable, match="needs blocked, which is Blocked") as err:
        evaluate(parse("count(blocked) == 2"), DATA)
    assert err.value.location == "blocked"


@pytest.mark.parametrize(
    "text",
    ["count(missing) == 0", "not exists(missing)", "none(missing, status == 'x')"],
)
def test_counting_reads_a_missing_location_as_no_rows(text: str) -> None:
    assert evaluate(parse(text), DATA) is True


@pytest.mark.parametrize(
    "text",
    [
        "sum(missing.amount) == 0",
        "min(missing.amount) == null",
        "max(missing.amount) == null",
        "unique(missing.id)",
        "all(missing, amount > 0)",
        "not any(missing, amount < 0)",
        "missing.status != 'captured'",
        "count(ledger where missing.status == 'x') == 0",
    ],
)
def test_anything_else_that_reads_a_missing_location_is_unavailable(text: str) -> None:
    # Each of these would pass on no rows, or on null, though nothing was there to check.
    with pytest.raises(Unavailable, match="needs missing, which is Missing"):
        evaluate(parse(text), DATA)


@pytest.mark.parametrize(
    "text",
    [
        "all(ledger where direction == 'refund', amount > 0)",
        "sum(ledger.amount where direction == 'refund') == 0",
        "unique(ledger.account where direction == 'refund')",
    ],
)
def test_a_filter_that_leaves_no_rows_of_a_found_location_still_evaluates(text: str) -> None:
    assert evaluate(parse(text), DATA) is True


@pytest.mark.parametrize(
    "text",
    [
        "count(ledger ==",
        "record.status === 'x'",
        "drop(ledger)",
        "sum(ledger) == 1",
        "count(ledger.amount) == 1",
        "all(ledger)",
        "record.status == 'unclosed",
        "record.status = 'x'",
        "count(a.b.c) == 1",
        "",
    ],
)
def test_an_expression_that_cannot_be_read_is_a_config_error(text: str) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        parse(text)


def test_a_bare_name_outside_a_row_needs_a_location() -> None:
    with pytest.raises(ConfigError, match=r"needs a location: write location\.status"):
        evaluate(parse("status == 'x'"), DATA)


def test_locations_used_lists_what_an_expression_reads() -> None:
    assert locations_used(parse("payment.amount == record.amount")) == {"payment", "record"}
    assert locations_used(parse("sum(ledger.amount where direction == 'x') > count(webhook)")) == {
        "ledger",
        "webhook",
    }
    # Inside where and all/any, a bare name is the row's own field.
    assert locations_used(parse("all(cards, controller.status == status)")) == {
        "cards",
        "controller",
    }


@pytest.mark.parametrize(
    ("op", "left", "right", "expected"),
    [
        ("==", None, None, True),
        ("!=", None, 1, True),
        ("<", None, 1, False),
        ("==", "500", Decimal("500.00"), True),
        ("==", True, 1, False),  # a boolean is not a number
        ("==", "abc", 500, False),
        ("!=", "abc", 500, True),
        ("<", "abc", 500, False),  # no order between text and a number
        ("<", date(2026, 1, 1), datetime(2026, 1, 2, tzinfo=UTC), True),
        ("<", "2026-01-01T00:00:00", datetime(2026, 1, 2, tzinfo=UTC), True),
        (">", datetime(2026, 1, 2), datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=1), True),
    ],
)
def test_compare(op: str, left: Any, right: Any, expected: bool) -> None:
    assert compare(op, left, right) is expected


def test_masking_covers_columns_in_any_case_and_nested_rest_fields() -> None:
    rows = [
        {
            "CUSTOMER_EMAIL": "a@example.com",
            "id": 1,
            "phone": None,
            "customer": {"email": "b@example.com", "name": "B"},
            "contacts": [{"email": "c@example.com"}, {"email": "d@example.com"}],
        },
    ]
    masked = mask_rows(rows, ["customer_email", "phone", "customer.email", "contacts.email"])
    assert masked == [
        {
            "CUSTOMER_EMAIL": MASKED,
            "id": 1,
            "phone": None,
            "customer": {"email": MASKED, "name": "B"},
            "contacts": [{"email": MASKED}, {"email": MASKED}],
        },
    ]
    assert rows[0]["CUSTOMER_EMAIL"] == "a@example.com"  # the originals are untouched


def test_to_json_keeps_numbers_and_dates_exact() -> None:
    assert to_json(
        {"a": Decimal("500.10"), "b": date(2026, 10, 31), "c": b"\x01", "d": [1, None]}
    ) == {
        "a": "500.10",
        "b": "2026-10-31",
        "c": "01",
        "d": [1, None],
    }


# --- unique and fan-out reads ---------------------------------------------------------------

CARDS = Data(
    cards=[
        {"card_id": "C204518", "status": "active", "@controller": [{"status": "active"}]},
        {"card_id": "C198220", "status": "active", "@controller": [{"status": "active"}]},
        {"card_id": "C177031", "status": "revoked", "@controller": [{"status": "active"}]},
        {"card_id": None, "status": "draft"},
        {"card_id": None, "status": "draft"},
    ],
    repeated=[{"n": 500}, {"n": "500.00"}, {"n": 7}],
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("unique(cards.card_id)", True),  # nulls are not values, so two nulls are no repeat
        ("unique(cards.status)", False),
        ("unique(cards.status where status != 'draft')", False),
        ("unique(cards.status where status == 'revoked')", True),
        ("unique(repeated.n)", False),  # 500 and '500.00' are the same value
        ("all(cards where card_id != null, controller.status == status)", False),
        ("any(cards where card_id != null, controller.status != status)", True),
        ("none(cards where status == 'active', controller.status != status)", True),
    ],
)
def test_unique_and_fan_out_reads(text: str, expected: bool) -> None:
    assert evaluate(parse(text), CARDS) is expected


def test_unique_takes_a_field() -> None:
    with pytest.raises(ConfigError, match=r"unique\(\) takes location.field"):
        parse("unique(cards)")


def test_duplicates_counts_repeats_by_meaning() -> None:
    assert duplicates(CARDS.rows("repeated"), "n") == {"500": 2}
