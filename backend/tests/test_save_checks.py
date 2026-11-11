# Author: @SaadZarook
"""Save checks of a draft probe, and its test run: nothing is saved."""

import re
from pathlib import Path
from typing import Any

import pytest

from probeowl.checks.service import check_draft
from probeowl.model.check import LoadedApplication
from probeowl.model.save_check import CheckStatus, SaveCheckReport
from tests.conftest import Setup
from tests.test_runs import PROBLEM_JSON, finished, no_lookups, runs_stored

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
PROBE = (EXAMPLES / "wallet" / "probes" / "topup_completed.yaml").read_text(encoding="utf-8")
ENTITY = (EXAMPLES / "wallet" / "entities" / "topup.yaml").read_text(encoding="utf-8")
RECORD = (
    "SELECT id AS topup_id, user_id, amount, currency, status, provider_payment_id,\n"
    "             ledger_txn_id, payment_method, app_version, customer_email, created_at\n"
    "      FROM topups WHERE id = :topup_id"
)
assert RECORD in ENTITY  # the tests below edit the record location's lookup


def entity_with(lookup: str, pii: str = "[customer_email]") -> str:
    edited = ENTITY.replace(RECORD, lookup)
    return re.sub(r"pii: \[customer_email\]", f"pii: {pii}", edited, count=1)


def wallet(setup: Setup) -> LoadedApplication:
    return setup.applications["wallet"]


def failed(report: SaveCheckReport) -> list[tuple[str, str | None, str]]:
    return [(c.check, c.location, c.message) for c in report.checks if c.status is CheckStatus.FAIL]


# --- The examples pass ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("application", "probe"),
    [
        ("wallet", "wallet/probes/topup_completed.yaml"),
        ("card_access", "card-access/probes/visitor_cards.yaml"),
    ],
)
def test_the_example_probes_pass_every_check(setup: Setup, application: str, probe: str) -> None:
    report = check_draft(
        setup.applications[application], (EXAMPLES / probe).read_text(encoding="utf-8")
    )
    assert failed(report) == []
    assert report.can_save
    assert [c.check for c in report.checks] == [
        "Expectations",
        "Environments",
        "Row ceiling",
        "Healthy sample",
        "Read-only SQL",
        "Time filter",
        "PII masked",
    ]


# --- Statement classifier -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lookup", "message"),
    [
        ("DELETE FROM topups WHERE id = :topup_id", "only a SELECT may run"),
        # T-SQL has no FOR UPDATE: refused as SQL Server cannot read it.
        (
            "SELECT id AS topup_id FROM topups WHERE id = :topup_id FOR UPDATE",
            "could not be parsed as sqlserver SQL",
        ),
        (
            "SELECT id AS topup_id FROM topups WHERE id = :topup_id; DROP TABLE topups",
            "single statement",
        ),
        ("SELECT id AS topup_id FROM topups WITH (NOLOCK) WHERE id = :topup_id", "hint"),
    ],
)
def test_a_lookup_that_is_not_a_single_read_only_select_fails(
    setup: Setup, lookup: str, message: str
) -> None:
    report = check_draft(wallet(setup), PROBE, entity_with(lookup))
    [(check, location, detail)] = [f for f in failed(report) if f[0] == "Read-only SQL"]
    assert (check, location) == ("Read-only SQL", "record")
    assert message in detail
    assert not report.can_save


def test_a_rest_lookup_that_is_not_a_get_fails(setup: Setup) -> None:
    entity = ENTITY.replace(
        "lookup: GET /payments/{{provider_payment_id}}",
        "lookup: POST /payments/{{provider_payment_id}}",
    )
    report = check_draft(wallet(setup), PROBE, entity)
    assert ("Read-only SQL", "payment", "only GET is sent; this lookup uses POST") in failed(report)


def test_a_row_limit_above_what_the_lookup_reads_cannot_be_saved(setup: Setup) -> None:
    # The record is a one location: it reads one row, and the guard asks for one more to see
    # whether there are more. Its own TOP 5 would be refused when it runs, so it is refused now.
    over = RECORD.replace("SELECT id AS topup_id", "SELECT TOP 5 id AS topup_id")
    report = check_draft(wallet(setup), PROBE, entity_with(over))
    assert (
        "Read-only SQL",
        "record",
        "the query's own row limit (5) is above the row cap of 2: lower it, or leave it out",
    ) in failed(report)
    within = RECORD.replace("SELECT id AS topup_id", "SELECT TOP 1 id AS topup_id")
    report = check_draft(wallet(setup), PROBE, entity_with(within))
    assert not [f for f in failed(report) if f[0] == "Read-only SQL"]


# --- Label rule: a Transaction table needs a key or a time window --------------------------


def test_a_query_on_a_transaction_table_without_a_time_filter_is_rejected(setup: Setup) -> None:
    # The M3 exit check: a clear message, and the draft cannot be saved.
    lookup = "SELECT id AS topup_id, status, customer_email FROM topups WHERE status = :status"
    report = check_draft(wallet(setup), PROBE, entity_with(lookup))
    assert (
        "Time filter",
        "record",
        (
            "topups is a Transaction table: filter on its time column created_at from a "
            "parameter (for example created_at >= :since), or look rows up by a key (a primary "
            "key, link or external key column = :parameter, or IN a short list)"
        ),
    ) in failed(report)
    assert not report.can_save


def time_failures(setup: Setup, where: str) -> list[str]:
    lookup = f"SELECT t.id AS topup_id, t.status, t.customer_email FROM topups t {where}"
    report = check_draft(wallet(setup), PROBE, entity_with(lookup))
    return [message for check, _, message in failed(report) if check == "Time filter"]


@pytest.mark.parametrize(
    "where",
    [
        # Equality on a marked key: =, or a short fixed IN list.
        "WHERE t.id = :topup_id",
        "WHERE t.id = 'tp_91f2c7'",
        "WHERE provider_payment_id IN (:a, :b)",  # an external key
        "WHERE t.id IN ('tp_1', 'tp_2', 'tp_3')",
        # A lower bound on the time column from a parameter, or relative to now.
        "WHERE status = :status AND created_at >= :since",
        "WHERE :since <= created_at",
        "WHERE created_at BETWEEN :start AND :end",
        "WHERE created_at > DATEADD(day, -7, GETDATE())",
        "WHERE created_at >= SYSUTCDATETIME()",
    ],
)
def test_a_key_or_a_bounded_time_window_satisfies_the_rule(setup: Setup, where: str) -> None:
    assert time_failures(setup, where) == []


@pytest.mark.parametrize(
    "where",
    [
        "WHERE t.id LIKE :pattern",  # not equality
        "WHERE t.id > :after",  # a range on a key
        "WHERE t.id <> :other",
        "WHERE t.id IN (" + ", ".join(f":k{i}" for i in range(11)) + ")",  # not a short list
        "WHERE t.id IN (SELECT topup_id FROM refunds)",  # not a fixed list
        "WHERE status = :status",  # equality, but status is not a key
        "WHERE created_at < :until",  # no lower bound: all of history
        "WHERE created_at >= :since OR status = :status",  # under an OR
    ],
)
def test_ranges_patterns_and_open_bounds_do_not_count(setup: Setup, where: str) -> None:
    [message] = time_failures(setup, where)
    assert message.startswith("topups is a Transaction table: filter on its time column")


@pytest.mark.parametrize(
    "where",
    [
        "WHERE created_at >= '2000-01-01'",
        "WHERE created_at BETWEEN '1970-01-01' AND :end",
        "WHERE created_at > CAST('2000-01-01' AS DATE)",
    ],
)
def test_a_constant_lower_bound_fails_with_its_own_message(setup: Setup, where: str) -> None:
    assert time_failures(setup, where) == [
        "topups is a Transaction table, and created_at is bounded by a fixed value, which can "
        "reach far into the past: bound it by a parameter (created_at >= :since) or relative "
        "to now"
    ]


# --- Label rule: PII columns are shown masked ----------------------------------------------------


@pytest.mark.parametrize(
    ("lookup", "pii", "name"),
    [
        (RECORD, "[]", "customer_email"),
        (
            RECORD.replace("customer_email,", "customer_email AS email,"),
            "[customer_email]",
            "email",
        ),
        ("SELECT * FROM topups WHERE id = :topup_id", "[]", "customer_email"),
    ],
)
def test_a_pii_column_selected_without_masking_fails(
    setup: Setup, lookup: str, pii: str, name: str
) -> None:
    report = check_draft(wallet(setup), PROBE, entity_with(lookup, pii))
    assert (
        "PII masked",
        "record",
        (
            f"customer_email is PII in the catalog (wallet_db.topups): add {name} to the "
            "location's pii so it is masked"
        ),
    ) in failed(report)


def test_the_alias_masked_passes(setup: Setup) -> None:
    lookup = RECORD.replace("customer_email,", "customer_email AS email,")
    report = check_draft(wallet(setup), PROBE, entity_with(lookup, "[email]"))
    assert [f for f in failed(report) if f[0] == "PII masked"] == []


# --- The draft itself ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("probe", "entity", "check", "message"),
    [
        ("probe: [unclosed", None, "Probe", "the YAML cannot be read"),
        ("connector: x\ntype: rest\nenvironments: {}\n", None, "Probe", "the draft is not a probe"),
        (
            PROBE.replace("count(ledger) == 2", "count(nope) == 2"),
            None,
            "Expectations",
            "reads nope, not a location of topup",
        ),
        (
            PROBE.replace("entity: topup", "entity: refund"),
            None,
            "Entity",
            "wallet has no entity refund",
        ),
        (
            PROBE.replace("owner:", "environments: [staging]\nowner:"),
            None,
            "Environments",
            "not environments of wallet: staging",
        ),
        (
            PROBE,
            ENTITY.replace("entity: topup", "entity: refund"),
            "Entity",
            "the probe is for topup, the draft entity is refund",
        ),
        (
            PROBE,
            ENTITY.replace("using: checkout_api", "using: nowhere"),
            "Connectors",
            "no connector named nowhere",
        ),
        (
            PROBE,
            ENTITY.replace(
                "FROM topups WHERE id = :topup_id", "FROM topups WHERE id = :topup_id OR id = 'x'"
            ),
            "Identifiers",
            "exactly one condition",
        ),
    ],
)
def test_a_draft_that_cannot_run_fails_with_the_reason(
    setup: Setup, probe: str, entity: str | None, check: str, message: str
) -> None:
    report = check_draft(wallet(setup), probe, entity)
    assert any(c == check and message in m for c, _, m in failed(report)), report.checks
    assert not report.can_save


# --- Through the API: checks and the test run --------------------------------------------------


def post_checks(setup: Setup, **body: Any) -> Any:
    return setup.client().post(
        "/api/applications/wallet/probes/checks", json={"probe": PROBE} | body
    )


def test_the_checks_route_returns_the_report_and_writes_nothing(setup: Setup) -> None:
    files = sorted(p.read_text(encoding="utf-8") for p in (EXAMPLES / "wallet").rglob("*.yaml"))
    response = post_checks(setup, environment="prod")
    assert response.status_code == 200, response.text
    report = response.json()
    assert (report["probe"], report["entity"], report["can_save"]) == (
        "topup_completed",
        "topup",
        True,
    )
    assert report["failed"] == 0
    # The store has no schema imports: the healthy sample compares wallet_db's time column.
    assert [c["message"] for c in report["checks"] if c["status"] != "pass"] == [
        "wallet_db's schema is not imported, so its time columns are not checked for offsets: "
        "import it on the Tables page"
    ]
    assert (
        sorted(p.read_text(encoding="utf-8") for p in (EXAMPLES / "wallet").rglob("*.yaml"))
        == files
    )
    assert no_lookups(setup)


def test_the_checks_route_reports_failures_as_a_200(setup: Setup) -> None:
    lookup = "SELECT id AS topup_id FROM topups WHERE status = :status"
    report = post_checks(setup, entity=entity_with(lookup)).json()
    assert report["can_save"] is False and report["failed"] >= 1


def test_an_unknown_environment_for_the_checks_is_a_404(setup: Setup) -> None:
    assert post_checks(setup, environment="staging").status_code == 404


def draft_run(setup: Setup, **changes: Any) -> Any:
    body = {
        "probe": PROBE,
        "environment": "prod",
        "value": "tp_91f2c7",
        "investigation": {"reason": "incident", "reference": "WALLET-482"},
    } | changes
    return setup.client().post("/api/applications/wallet/probes/test-runs", json=body)


def test_a_draft_runs_without_being_saved_and_says_it_was_a_draft(setup: Setup) -> None:
    draft = PROBE.replace("count(ledger) == 2", "count(ledger) == 0")
    response = draft_run(setup, probe=draft)
    assert response.status_code == 202, response.text
    assert response.headers["Location"] == f"/api/runs/{response.json()['id']}"
    run = finished(setup.client(), response.json()["id"])
    assert run["probe_version"].startswith("draft:sha256:")
    assert run["probe"] == "topup_completed"
    # The draft's expectation ran, not the saved one's.
    assert {e["expectation"]: e["result"] for e in run["expectations"]}[
        "count(ledger) == 0"
    ] == "pass"
    assert setup.applications["wallet"].probes["topup_completed"].steps[0].expect != [
        "count(ledger) == 0"
    ]


def test_a_draft_entity_is_used_for_the_test_run(setup: Setup) -> None:
    entity = ENTITY.replace(
        "FROM topups WHERE id = :topup_id", "FROM topups WHERE id = :topup_id AND 1 = 1"
    )
    run = finished(setup.client(), draft_run(setup, entity=entity).json()["id"])
    [lookup, _] = setup.fakes["wallet_db"].calls[0]
    assert "1 = 1" in lookup
    assert run["status"] == "failed"  # tp_91f2c7's planted missing ledger


def test_a_draft_that_fails_a_check_does_not_run(setup: Setup) -> None:
    lookup = "SELECT id AS topup_id FROM topups WHERE status = :status"
    response = draft_run(setup, entity=entity_with(lookup))
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["detail"].startswith("the draft does not pass its save checks: ")
    # Every failure is named: the time filter, and the identifier lookups built on the record.
    fields = {e["field"] for e in response.json()["errors"]}
    assert "Time filter: record" in fields
    assert "Identifiers: identifier provider_payment_id" in fields
    assert no_lookups(setup) and runs_stored(setup) == 0


def test_a_test_run_in_production_needs_a_reference(setup: Setup) -> None:
    response = draft_run(setup, investigation={"reason": "incident"})
    assert response.status_code == 422
    assert "reference" in response.json()["detail"]
    assert no_lookups(setup)
