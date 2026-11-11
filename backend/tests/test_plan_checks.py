# Author: @SaadZarook
"""Plan checks: index use per lookup and per identifier, from each dialect's EXPLAIN."""

from pathlib import Path
from typing import Any

import pytest

from probeowl.checks.plans import PLACEHOLDER_NOTE
from probeowl.checks.service import check_draft
from probeowl.connectors.mariadb import explain_step
from probeowl.connectors.oracle import plan_table_step
from probeowl.connectors.plan import Access, PlanStep
from probeowl.connectors.sqlserver import plan_sql, showplan_steps
from probeowl.errors import ConnectorError
from probeowl.guards.dialects import SQLSERVER
from probeowl.guards.statement import prepare
from probeowl.model.save_check import CheckStatus, SaveCheck
from tests.conftest import Setup
from tests.plan_fakes import Plans
from tests.test_save_checks import PROBE, entity_with, wallet

EXAMPLES = Path(__file__).parents[2] / "examples"


def plan_rows(report: Any) -> list[SaveCheck]:
    return [c for c in report.checks if c.environment is not None or c.check == "Index use"]


def scan_on(table: str, rows: int | None = 1_000_000) -> Plans:
    """Plans that seek, except a full scan wherever the lookup reads table."""

    def planner(sql: str) -> list[PlanStep]:
        if f"FROM {table}" in sql or f"FROM dbo.{table}" in sql:
            return [PlanStep("Clustered Index Scan", Access.SCAN, table, "pk", rows)]
        return [PlanStep("Index Seek", Access.INDEX, "t", "ix_key")]

    return Plans(planner)


# --- What the plans say ------------------------------------------------------------------------


def test_every_sql_lookup_and_identifier_lookup_gets_its_plan_read(setup: Setup) -> None:
    plans = Plans()
    report = check_draft(wallet(setup), PROBE, None, "prod", plans)
    rows = plan_rows(report)
    # record, webhook, ledger, the identifier lookups and the healthy sample's; REST has no plan.
    assert [(c.check, c.location) for c in rows] == [
        ("wallet_db plan · SQL Server", "record"),
        ("wallet_db plan · SQL Server", "webhook"),
        ("ledger_db plan · Oracle", "ledger"),
        ("Identifier index", "identifier provider_payment_id"),
        ("Identifier index", "identifier ledger_txn_id"),
        ("wallet_db plan · SQL Server", "healthy_sample"),
        ("wallet_db plan · SQL Server", "healthy_sample.same_payment_method"),
    ]
    assert all(c.status is CheckStatus.PASS and c.environment == "prod" for c in rows)
    assert rows[0].message == f"Index Seek on ix_key ({PLACEHOLDER_NOTE})"
    # Oracle's EXPLAIN PLAN does not peek at values, so it has no note.
    assert rows[2].message == "Index Seek on ix_key"
    # One connection per connector, all closed.
    assert sorted(set(plans.opened)) == [("ledger_db", "prod"), ("wallet_db", "prod")]
    assert plans.closed == 2
    assert report.can_save


def test_a_lookup_that_reads_a_whole_large_table_fails(setup: Setup) -> None:
    lookup = "SELECT id AS topup_id, customer_email FROM topups WHERE id = :topup_id"
    report = check_draft(wallet(setup), PROBE, entity_with(lookup), "prod", scan_on("topups"))
    [record] = [c for c in plan_rows(report) if c.location == "record"]
    assert record.status is CheckStatus.FAIL
    assert record.message == (
        "Clustered Index Scan of topups: the lookup would read all of topups. Index the column "
        f"it filters on, or look rows up by a key ({PLACEHOLDER_NOTE})"
    )
    assert not report.can_save


def test_an_identifier_whose_lookup_scans_fails_the_identifier_index_rule(setup: Setup) -> None:
    def planner(sql: str) -> list[PlanStep]:
        if "provider_payment_id = " in sql:
            return [PlanStep("Clustered Index Scan", Access.SCAN, "topups", "pk", 48_000_000)]
        return [PlanStep("Index Seek", Access.INDEX, "topups", "pk")]

    report = check_draft(wallet(setup), PROBE, None, "prod", Plans(planner))
    [rule] = [c for c in plan_rows(report) if c.location == "identifier provider_payment_id"]
    assert (rule.check, rule.status) == ("Identifier index", CheckStatus.FAIL)
    assert rule.message == (
        "identifier provider_payment_id: Clustered Index Scan of topups. Looking a subject up by "
        "provider_payment_id would read all of topups: index the column behind "
        f"record.provider_payment_id (provider_payment_id) ({PLACEHOLDER_NOTE})"
    )
    assert not report.can_save


def test_a_small_table_read_whole_only_warns(setup: Setup) -> None:
    report = check_draft(wallet(setup), PROBE, None, "uat", scan_on("provider_webhooks", 40))
    [webhook] = [c for c in plan_rows(report) if c.location == "webhook"]
    assert webhook.status is CheckStatus.WARN
    assert webhook.message == (
        "Clustered Index Scan of provider_webhooks (about 40 rows): fine while provider_webhooks "
        f"is small; index it before it grows ({PLACEHOLDER_NOTE})"
    )
    assert report.can_save


def test_an_alias_in_the_plan_is_named_as_its_table(setup: Setup) -> None:
    lookup = "SELECT t.id AS topup_id, t.customer_email FROM topups t WHERE t.id = :topup_id"

    def planner(sql: str) -> list[PlanStep]:
        return [PlanStep("full table scan (ALL)", Access.SCAN, "t", None, 5_000)]

    report = check_draft(wallet(setup), PROBE, entity_with(lookup), "prod", Plans(planner))
    [record] = [c for c in plan_rows(report) if c.location == "record"]
    assert "the lookup would read all of topups" in record.message


# --- When plans cannot be read --------------------------------------------------------------------


def test_without_an_environment_plans_are_not_read_and_it_says_so(setup: Setup) -> None:
    plans = Plans()
    report = check_draft(wallet(setup), PROBE, None, None, plans)
    [row] = plan_rows(report)
    assert (row.check, row.status, row.message) == (
        "Index use",
        CheckStatus.WARN,
        "not checked: choose an environment to read plans in",
    )
    assert plans.opened == []


def test_a_connector_not_bound_there_warns(setup: Setup) -> None:
    report = check_draft(
        wallet(setup), PROBE, None, "prod", Plans(unbound=frozenset({"ledger_db"}))
    )
    [ledger] = [c for c in plan_rows(report) if c.location == "ledger"]
    assert (ledger.status, ledger.message) == (
        CheckStatus.WARN,
        "index use not checked: ledger_db has no binding in prod",
    )


def test_a_plan_that_cannot_be_read_warns_with_the_reason(setup: Setup) -> None:
    def refused(sql: str) -> list[PlanStep]:
        raise ConnectorError("SQL Server: SHOWPLAN permission denied in database 'wallet'")

    report = check_draft(wallet(setup), PROBE, None, "prod", Plans(refused))
    [record] = [c for c in plan_rows(report) if c.location == "record"]
    assert (record.status, record.message) == (
        CheckStatus.WARN,
        "index use not checked: SQL Server: SHOWPLAN permission denied in database 'wallet'",
    )
    assert report.can_save  # the plan reader never blocks saving by itself


def test_a_lookup_the_classifier_refuses_gets_no_plan(setup: Setup) -> None:
    plans = Plans()
    check_draft(
        wallet(setup), PROBE, entity_with("DELETE FROM topups WHERE id = :topup_id"), "prod", plans
    )
    assert not [sql for _, _, sql in plans.asked if "DELETE" in sql]


def test_the_checks_route_reads_plans_in_the_environment_given(setup: Setup) -> None:
    setup.plans = scan_on("topups")
    report = (
        setup.client()
        .post("/api/applications/wallet/probes/checks", json={"probe": PROBE, "environment": "uat"})
        .json()
    )
    record = next(c for c in report["checks"] if c["location"] == "record" and c["environment"])
    assert (record["status"], record["environment"]) == ("fail", "uat")
    assert report["can_save"] is False


# --- Each dialect's plan, read --------------------------------------------------------------------

SHOWPLAN = """<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan">
<BatchSequence><Batch><Statements><StmtSimple><QueryPlan>
<RelOp PhysicalOp="Top" LogicalOp="Top"><Top>
<RelOp PhysicalOp="Nested Loops" LogicalOp="Inner Join"><NestedLoops>
<RelOp PhysicalOp="Index Seek" LogicalOp="Index Seek" TableCardinality="48000000">
<IndexScan><Object Database="[wallet]" Schema="[dbo]" Table="[topups]"
 Index="[IX_topups_provider_payment_id]"/></IndexScan></RelOp>
<RelOp PhysicalOp="Key Lookup" LogicalOp="Key Lookup">
<IndexScan Lookup="1"><Object Table="[topups]" Index="[PK_topups]"/></IndexScan></RelOp>
</NestedLoops></RelOp>
<RelOp PhysicalOp="Clustered Index Scan" LogicalOp="Clustered Index Scan" TableCardinality="120">
<IndexScan><Object Table="[currencies]" Index="[PK_currencies]"/></IndexScan></RelOp>
</Top></RelOp>
</QueryPlan></StmtSimple></Statements></Batch></BatchSequence></ShowPlanXML>"""


def test_sql_server_showplan_operators() -> None:
    assert showplan_steps(SHOWPLAN) == [
        PlanStep("Index Seek", Access.INDEX, "topups", "IX_topups_provider_payment_id", 48_000_000),
        PlanStep("Key Lookup", Access.OTHER, "topups", "PK_topups", None),
        PlanStep("Clustered Index Scan", Access.SCAN, "currencies", "PK_currencies", 120),
    ]


@pytest.mark.parametrize(
    ("row", "step"),
    [
        (
            {"table": "c", "type": "ref", "key": "ix_cards_visitor", "rows": 3},
            PlanStep("ref", Access.INDEX, "c", "ix_cards_visitor", 3),
        ),
        (
            {"table": "cards", "type": "const", "key": "PRIMARY", "rows": 1},
            PlanStep("const", Access.INDEX, "cards", "PRIMARY", 1),
        ),
        (
            {"table": "cards", "type": "ALL", "key": None, "rows": 32},
            PlanStep("full table scan (ALL)", Access.SCAN, "cards", None, 32),
        ),
        (
            {"table": "cards", "type": "index", "key": "ix_status", "rows": 32},
            PlanStep("full index scan (index)", Access.SCAN, "cards", "ix_status", 32),
        ),
        (
            {"table": None, "type": None, "key": None, "rows": None, "extra": "No tables used"},
            PlanStep("No tables used", Access.OTHER, None),
        ),
    ],
)
def test_mariadb_explain_rows(row: dict[str, Any], step: PlanStep) -> None:
    assert explain_step(row) == step


@pytest.mark.parametrize(
    ("row", "step"),
    [
        (
            ("TABLE ACCESS", "FULL", "LEDGER_ENTRIES"),
            PlanStep("TABLE ACCESS FULL", Access.SCAN, "ledger_entries"),
        ),
        (
            ("INDEX", "RANGE SCAN", "LEDGER_TXN_IDX"),
            PlanStep("INDEX RANGE SCAN", Access.INDEX, None, "ledger_txn_idx"),
        ),
        (
            ("INDEX", "UNIQUE SCAN", "PK_LEDGER"),
            PlanStep("INDEX UNIQUE SCAN", Access.INDEX, None, "pk_ledger"),
        ),
        (
            ("INDEX", "FAST FULL SCAN", "LEDGER_TXN_IDX"),
            PlanStep("INDEX FAST FULL SCAN", Access.SCAN, None, "ledger_txn_idx"),
        ),
        (
            ("TABLE ACCESS", "BY INDEX ROWID BATCHED", "LEDGER_ENTRIES"),
            PlanStep("TABLE ACCESS BY INDEX ROWID BATCHED", Access.OTHER, "ledger_entries"),
        ),
        (("SELECT STATEMENT", None, None), PlanStep("SELECT STATEMENT", Access.OTHER, None)),
    ],
)
def test_oracle_plan_table_rows(row: tuple[str, str | None, str | None], step: PlanStep) -> None:
    assert plan_table_step(*row) == step


def test_sql_server_plans_use_typed_literals_never_the_values() -> None:
    query = prepare(
        "SELECT id FROM topups WHERE id = :topup_id AND user_id IN (:a, :b) AND note LIKE '?%'",
        {"topup_id": "tp_1'; DROP TABLE topups--", "a": 5, "b": "x"},
        SQLSERVER,
        2,
    )
    sql = plan_sql(query, {"b": "nvarchar(20)"})
    assert sql == (
        "SELECT TOP 2 id FROM topups WHERE id = '0' AND user_id IN (0, N'0') AND note LIKE '?%' "
        "OPTION(MAXDOP 1)"
    )
    assert "DROP" not in sql


def test_mariadb_plans_carry_the_placeholder_note(setup: Setup) -> None:
    probe = (EXAMPLES / "card-access" / "probes" / "visitor_cards.yaml").read_text(encoding="utf-8")
    report = check_draft(setup.applications["card_access"], probe, None, "prod", Plans())
    rows = [c for c in plan_rows(report) if c.check.startswith("access_db plan")]
    assert rows
    assert all(c.message == f"Index Seek on ix_key ({PLACEHOLDER_NOTE})" for c in rows)


def test_the_placeholder_note_says_what_it_should() -> None:
    assert PLACEHOLDER_NOTE == (
        "planned with placeholder values; the real plan can differ for unusual values"
    )


def test_a_plan_that_could_not_be_read_carries_no_placeholder_note(setup: Setup) -> None:
    def refused(sql: str) -> list[PlanStep]:
        raise ConnectorError("permission denied")

    report = check_draft(wallet(setup), PROBE, None, "prod", Plans(refused))
    [record] = [c for c in plan_rows(report) if c.location == "record"]
    assert PLACEHOLDER_NOTE not in record.message
