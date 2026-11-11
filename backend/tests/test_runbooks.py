# Author: @SaadZarook
"""Runbooks through the API: the server labels each step and decides whether it may run."""

from datetime import date, timedelta
from pathlib import Path
from shutil import copytree
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from probeowl.api.app import create_app
from probeowl.checks.rules import folder_failures
from probeowl.cli import main as cli
from probeowl.guards.dialects import MARIADB, ORACLE, SQLSERVER
from probeowl.guards.statement import is_single_select
from probeowl.model.check import LoadedApplication, check_folder, load_applications
from probeowl.model.runbook import ReadQueryStep, Review, Runbook, WriteQueryStep, sql_sha256
from probeowl.runbooks import RELABELLED, matching, runbook_view
from tests.conftest import Setup
from tests.local_client import local_client
from tests.test_read_routes import binding_values
from tests.test_runs import finished, start

EXAMPLES = Path(__file__).parents[2] / "examples"
RUNBOOK = EXAMPLES / "wallet" / "runbooks" / "topup_not_credited.yaml"
LIST = "/api/applications/wallet/runbooks"
TODAY = date(2026, 11, 7)
SELECT = "SELECT id, status FROM outbox_events WHERE aggregate_id = :topup_id"


@pytest.fixture
def wallet() -> LoadedApplication:
    return load_applications(EXAMPLES)["wallet"]


def reviewed(sql: str, when: date = TODAY) -> Review:
    return Review(by="s.khan", date=when, sql_sha256=sql_sha256(sql))


def only_step(wallet: LoadedApplication, step: Any, today: date = TODAY) -> Any:
    runbook = wallet.runbooks["topup_not_credited"].model_copy(update={"steps": [step]})
    return runbook_view(wallet, runbook, today).steps[0]


def read_step(sql: str = SELECT, review: Review | None = None, using: str = "wallet_db") -> Any:
    return ReadQueryStep(
        kind="read_query", id="a-read", title="A read", using=using, sql=sql, reviewed=review
    )


# --- The example --------------------------------------------------------------------------------


def test_the_example_has_each_kind_of_step_labelled(wallet: LoadedApplication) -> None:
    steps = runbook_view(wallet, wallet.runbooks["topup_not_credited"], TODAY).steps
    assert [(s.kind, s.runnable) for s in steps] == [
        ("read_query", True),
        ("read_query", False),
        ("external_query", False),
        ("manual", False),
        ("write_query", False),
        ("probe", True),
    ]
    assert steps[1].why_copy_only == "not reviewed yet"  # type: ignore[union-attr]


def test_the_examples_pass_owl_check(wallet: LoadedApplication) -> None:
    assert folder_failures(wallet) == []


# --- The statement decides, not its author ------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "rules"),
    [
        ("UPDATE t SET a = 1 WHERE id = :id", SQLSERVER),
        ("DELETE FROM t WHERE id = :id", MARIADB),
        ("INSERT INTO t (a) VALUES (1)", SQLSERVER),
        ("MERGE INTO t USING s ON t.a = s.a WHEN MATCHED THEN DELETE", ORACLE),
        ("DROP TABLE t", SQLSERVER),
        ("TRUNCATE TABLE t", SQLSERVER),
        ("SELECT * INTO #t FROM s", SQLSERVER),
        ("WITH d AS (DELETE FROM t RETURNING id) SELECT id FROM d", MARIADB),
        ("SELECT 1; DELETE FROM t", SQLSERVER),
        ("EXEC sp_who", SQLSERVER),
        ("BEGIN DELETE FROM t; END;", ORACLE),
        ("not sql at all (", SQLSERVER),
    ],
)
def test_anything_but_a_single_select_is_a_write(sql: str, rules: Any) -> None:
    assert is_single_select(sql, rules) is False


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT a FROM t UNION SELECT a FROM s",
        "WITH c AS (SELECT 1 AS a) SELECT a FROM c",
    ],
)
def test_a_single_select_is_a_read(sql: str) -> None:
    assert is_single_select(sql, SQLSERVER) is True


def test_a_read_step_that_changes_data_becomes_a_write_step(wallet: LoadedApplication) -> None:
    sql = "UPDATE outbox_events SET status = 'pending' WHERE aggregate_id = :topup_id"
    # A review changes nothing: the label cannot be lowered.
    view = only_step(wallet, read_step(sql, reviewed(sql)))
    assert view.kind == "write_query"
    assert view.runnable is False
    assert view.relabelled is True
    assert view.requires == RELABELLED


def test_a_write_step_stays_one_even_if_it_is_a_select(wallet: LoadedApplication) -> None:
    step = WriteQueryStep(
        kind="write_query",
        id="a-write",
        title="A write",
        using="wallet_db",
        sql=SELECT,
        requires="approval",
    )
    view = only_step(wallet, step)
    assert view.kind == "write_query"
    assert view.runnable is False
    assert view.relabelled is False


def test_owl_check_refuses_a_read_step_that_changes_data(wallet: LoadedApplication) -> None:
    runbook = wallet.runbooks["topup_not_credited"]
    sql = "DELETE FROM outbox_events WHERE id = :id"
    wallet.runbooks["topup_not_credited"] = runbook.model_copy(update={"steps": [read_step(sql)]})
    assert folder_failures(wallet) == [
        "wallet: runbook topup_not_credited: step 'A read' is not a single SELECT: "
        "make it a write_query"
    ]


# --- When a read step may run -------------------------------------------------------------------


def test_a_reviewed_read_that_passes_its_checks_may_run(wallet: LoadedApplication) -> None:
    view = only_step(wallet, read_step(review=reviewed(SELECT)))
    assert (view.runnable, view.why_copy_only) == (True, None)


def test_an_unreviewed_read_is_copy_only_and_says_why(wallet: LoadedApplication) -> None:
    view = only_step(wallet, read_step())
    assert (view.runnable, view.why_copy_only) == (False, "not reviewed yet")


def test_changing_reviewed_sql_sends_it_back_to_copy_only(wallet: LoadedApplication) -> None:
    edited = f"{SELECT} AND status = 'failed'"
    view = only_step(wallet, read_step(edited, reviewed(SELECT)))
    assert (view.runnable, view.why_copy_only) == (
        False,
        "its SQL changed after it was reviewed",
    )


def test_line_endings_are_not_a_change_of_sql() -> None:
    assert sql_sha256("SELECT 1\nFROM t\n") == sql_sha256("  SELECT 1\r\nFROM t")
    assert sql_sha256("SELECT 1") != sql_sha256("SELECT 2")


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        ("SELECT id FROM outbox_events WITH (NOLOCK) WHERE aggregate_id = :a", "table hint"),
        ("SELECT id FROM #t", "temp tables"),
    ],
)
def test_a_reviewed_read_that_fails_the_guard_is_copy_only(
    wallet: LoadedApplication, sql: str, why: str
) -> None:
    view = only_step(wallet, read_step(sql, reviewed(sql)))
    assert view.kind == "read_query"
    assert view.runnable is False
    assert why in view.why_copy_only


def test_a_reviewed_read_with_no_time_bound_is_copy_only(wallet: LoadedApplication) -> None:
    sql = "SELECT id FROM topups WHERE status = :status"
    view = only_step(wallet, read_step(sql, reviewed(sql)))
    assert view.runnable is False
    assert "Transaction table" in view.why_copy_only


def test_a_reviewed_read_selecting_pii_is_copy_only(wallet: LoadedApplication) -> None:
    sql = "SELECT customer_email FROM topups WHERE id = :topup_id"
    view = only_step(wallet, read_step(sql, reviewed(sql)))
    assert view.runnable is False
    assert "customer_email is PII" in view.why_copy_only


def test_a_probe_step_may_run_only_if_the_probe_exists(wallet: LoadedApplication) -> None:
    del wallet.probes["topup_completed"]
    steps = runbook_view(wallet, wallet.runbooks["topup_not_credited"], TODAY).steps
    assert [s.runnable for s in steps if s.kind == "probe"] == [False]


# --- The reviewed block --------------------------------------------------------------------------


def test_a_review_reads_from_yaml() -> None:
    parsed = yaml.safe_load(f"by: s.khan\ndate: 2026-10-12\nsql_sha256: {'a' * 64}\n")
    assert Review.model_validate(parsed).date == date(2026, 10, 12)


def test_a_review_needs_a_real_hash() -> None:
    with pytest.raises(ValidationError):
        Review(by="s.khan", date=TODAY, sql_sha256="abc")


def test_the_example_review_really_matches_its_sql(wallet: LoadedApplication) -> None:
    [step] = [
        s
        for s in wallet.runbooks["topup_not_credited"].steps
        if isinstance(s, ReadQueryStep) and s.reviewed
    ]
    assert step.reviewed is not None
    assert step.reviewed.sql_sha256 == sql_sha256(step.sql)


def test_owl_runbook_hash_prints_what_a_review_needs() -> None:
    result = CliRunner().invoke(cli.app, ["runbook", "hash", str(RUNBOOK)])
    assert result.exit_code == 0
    assert "Is the ledger posting stuck in the outbox?" in result.output
    [first, *_] = result.output.splitlines()
    assert len(first.split()[0]) == 64


def test_owl_runbook_hash_refuses_what_is_not_a_runbook() -> None:
    other = EXAMPLES / "wallet" / "entities" / "topup.yaml"
    result = CliRunner().invoke(cli.app, ["runbook", "hash", str(other)])
    assert result.exit_code == 1
    assert "is not a runbook" in result.output


def test_a_step_with_sql_needs_a_sql_connector(tmp_path: Path) -> None:
    copytree(EXAMPLES, tmp_path / "config")
    path = tmp_path / "config" / "wallet" / "runbooks" / "topup_not_credited.yaml"
    text = path.read_text(encoding="utf-8").replace("using: ledger_db", "using: checkout_api")
    path.write_text(text, encoding="utf-8")
    assert "has SQL: use a SQL connector" in str(check_folder(tmp_path / "config").findings)


# --- The 90-day flag ----------------------------------------------------------------------------


def due(wallet: LoadedApplication, today: date) -> bool:
    return runbook_view(wallet, wallet.runbooks["topup_not_credited"], today).review_due


def test_the_flag_counts_from_the_review_date(wallet: LoadedApplication) -> None:
    when = date(2026, 10, 12)
    assert due(wallet, when + timedelta(days=90)) is False
    assert due(wallet, when + timedelta(days=91)) is True


def test_with_no_reviewed_step_it_counts_from_last_verified(wallet: LoadedApplication) -> None:
    runbook = wallet.runbooks["topup_not_credited"]
    wallet.runbooks["topup_not_credited"] = runbook.model_copy(
        update={"steps": [read_step()], "last_verified": TODAY - timedelta(days=91)}
    )
    assert due(wallet, TODAY) is True


def test_a_recent_last_verified_does_not_hide_an_old_review(wallet: LoadedApplication) -> None:
    runbook = wallet.runbooks["topup_not_credited"]
    old = reviewed(SELECT, TODAY - timedelta(days=200))
    wallet.runbooks["topup_not_credited"] = runbook.model_copy(
        update={"steps": [read_step(review=old)], "last_verified": TODAY}
    )
    assert due(wallet, TODAY) is True


# --- Search and drift ---------------------------------------------------------------------------


def names(wallet: LoadedApplication, **filters: Any) -> list[str]:
    return [r.runbook for r in matching(wallet, wallet.runbooks.values(), **filters)]


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"q": "wallet not CREDITED"},
        {"q": "ledger missing"},
        {"q": "provider dashboard"},
        {"entity": "topup"},
        {"table": "OUTBOX_EVENTS"},
        {"identifier": "topup_id"},
        {"q": "credited", "entity": "topup", "table": "outbox_events"},
    ],
)
def test_it_is_found_by_any_of_its_filters(wallet: LoadedApplication, filters: Any) -> None:
    assert names(wallet, **filters) == ["topup_not_credited"]


@pytest.mark.parametrize(
    "filters",
    [
        {"q": "nothing like this"},
        {"entity": "visitor"},
        {"table": "topups"},
        {"identifier": "ledger_txn_id"},
        {"q": "credited", "entity": "visitor"},
    ],
)
def test_a_filter_that_does_not_match_leaves_it_out(
    wallet: LoadedApplication, filters: Any
) -> None:
    assert names(wallet, **filters) == []


def test_it_names_its_tables_and_those_that_drifted(wallet: LoadedApplication) -> None:
    runbook = wallet.runbooks["topup_not_credited"]
    view = runbook_view(wallet, runbook, TODAY, {("wallet_db", "outbox_events"), ("x", "y")})
    assert "wallet_db.outbox_events" in view.tables
    assert view.drifted_tables == ["wallet_db.outbox_events"]


# --- Through the API ----------------------------------------------------------------------------


def test_the_list_and_one_runbook(setup: Setup) -> None:
    api = setup.client()
    listed = api.get(LIST).json()
    assert [r["runbook"] for r in listed] == ["topup_not_credited"]
    one = api.get(f"{LIST}/topup_not_credited").json()
    assert one == listed[0]
    assert [s["runnable"] for s in one["steps"] if s["kind"] == "write_query"] == [False]


def test_the_filters_reach_the_list(setup: Setup) -> None:
    api = setup.client()
    assert len(api.get(LIST, params={"q": "credited"}).json()) == 1
    assert api.get(LIST, params={"q": "nope"}).json() == []
    assert api.get(LIST, params={"entity": "visitor"}).json() == []


def test_what_does_not_exist_is_not_found(setup: Setup) -> None:
    api = setup.client()
    assert api.get("/api/applications/nope/runbooks").status_code == 404
    missing = api.get(f"{LIST}/nope")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/problem+json"
    assert "no runbook named nope" in missing.json()["detail"]


def test_an_application_with_no_runbooks_lists_none(setup: Setup) -> None:
    assert setup.client().get("/api/applications/card_access/runbooks").json() == []


def test_a_runbook_never_says_where_or_who(setup: Setup) -> None:
    text = setup.client().get(LIST).text
    assert not {value for value in binding_values(setup.applications) if value in text}


def test_with_no_catalog_service_nothing_is_called_drifted(setup: Setup) -> None:
    api = local_client(create_app(setup.service()))
    assert api.get(LIST).json()[0]["drifted_tables"] == []


def test_runbook_yaml_cannot_say_it_was_reviewed_and_runnable(wallet: LoadedApplication) -> None:
    data = yaml.safe_load(RUNBOOK.read_text(encoding="utf-8"))
    data["steps"][0]["runnable"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Runbook.model_validate(data)


# --- Step IDs -----------------------------------------------------------------------------------


def example_data() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(RUNBOOK.read_text(encoding="utf-8"))
    return data


def test_the_example_says_each_steps_id(wallet: LoadedApplication) -> None:
    steps = runbook_view(wallet, wallet.runbooks["topup_not_credited"], TODAY).steps
    assert [s.id for s in steps] == [
        "check-outbox",
        "check-locks",
        "ledger-errors",
        "confirm-capture",
        "requeue-posting",
        "revalidate",
    ]


def test_a_relabelled_step_keeps_its_id(wallet: LoadedApplication) -> None:
    sql = "DELETE FROM outbox_events WHERE id = :id"
    assert only_step(wallet, read_step(sql)).id == "a-read"


def test_a_step_needs_an_id() -> None:
    data = example_data()
    del data["steps"][0]["id"]
    with pytest.raises(ValidationError, match="id"):
        Runbook.model_validate(data)


def test_step_ids_must_be_unique() -> None:
    data = example_data()
    data["steps"][1]["id"] = data["steps"][0]["id"]
    with pytest.raises(ValidationError, match="step ids must be unique; repeated: check-outbox"):
        Runbook.model_validate(data)


@pytest.mark.parametrize("bad", ["1", "Check-Outbox", "check_outbox", "check outbox", "", "a" * 65])
def test_a_step_id_is_a_short_slug(bad: str) -> None:
    data = example_data()
    data["steps"][0]["id"] = bad
    with pytest.raises(ValidationError):
        Runbook.model_validate(data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("    id: check-locks\n", "step ids must be unique"),
        ("", "id"),
    ],
)
def test_owl_check_refuses_a_missing_or_repeated_id(
    tmp_path: Path, change: str, message: str
) -> None:
    copytree(EXAMPLES, tmp_path / "config")
    path = tmp_path / "config" / "wallet" / "runbooks" / "topup_not_credited.yaml"
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "    id: check-outbox\n" in text
    path.write_text(text.replace("    id: check-outbox\n", change), encoding="utf-8")
    result = CliRunner().invoke(cli.app, ["check", str(tmp_path / "config"), "--no-connect"])
    assert result.exit_code == 1
    assert message in result.output


# --- Suggestions --------------------------------------------------------------------------------


def suggestions_for(setup: Setup, value: str) -> Any:
    api = setup.client()
    run_id = start(api, value=value)
    finished(api, run_id)
    return api, run_id, api.get(f"/api/runs/{run_id}/runbooks").json()


def test_a_failing_location_suggests_its_runbook(setup: Setup) -> None:
    _, _, found = suggestions_for(setup, "tp_91f2c7")
    assert [s["runbook"]["runbook"] for s in found] == ["topup_not_credited"]
    assert found[0]["matched"] == ["ledger.missing"]
    assert found[0]["runbook"]["steps"][0]["id"] == "check-outbox"


def test_a_passing_run_suggests_nothing(setup: Setup) -> None:
    _, _, found = suggestions_for(setup, "tp_91f2b9")
    assert found == []


def test_another_outcome_of_the_location_does_not_match(setup: Setup) -> None:
    # Its ledger is Blocked by a missing link, not Missing.
    _, _, found = suggestions_for(setup, "tp_93b001")
    assert found == []


def test_the_closest_match_comes_first(setup: Setup) -> None:
    runbooks = setup.applications["wallet"].runbooks
    example = runbooks["topup_not_credited"]
    two = example.applies_to.model_copy(update={"when": ["ledger.missing", "record.found"]})
    runbooks["z_closer"] = example.model_copy(update={"runbook": "z_closer", "applies_to": two})
    _, _, found = suggestions_for(setup, "tp_91f2c7")
    assert [s["runbook"]["runbook"] for s in found] == ["z_closer", "topup_not_credited"]
    assert found[0]["matched"] == ["ledger.missing", "record.found"]


def test_a_runbook_for_another_entity_is_never_suggested(setup: Setup) -> None:
    runbooks = setup.applications["wallet"].runbooks
    example = runbooks["topup_not_credited"]
    other = example.applies_to.model_copy(update={"entity": "visitor"})
    runbooks["topup_not_credited"] = example.model_copy(update={"applies_to": other})
    _, _, found = suggestions_for(setup, "tp_91f2c7")
    assert found == []


def test_suggesting_for_an_unknown_run_is_not_found(setup: Setup) -> None:
    assert setup.client().get("/api/runs/nope/runbooks").status_code == 404
