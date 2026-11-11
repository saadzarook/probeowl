# Author: @SaadZarook
"""Time offsets: a time with an offset is never compared with a column that stores none."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from probeowl.checks.service import check_draft
from probeowl.checks.sql_shape import Comparison, Conversion, comparisons
from probeowl.checks.time_offsets import (
    TIME_OFFSET,
    ColumnTypes,
    TypedTime,
    column_types,
    has_offset,
    typed_time,
    unchecked_without_imports,
)
from probeowl.model.save_check import CheckStatus, SaveCheck
from probeowl.model.schema import ColumnSchema, SchemaSnapshot, TableSchema
from probeowl.store.schemas import save_snapshot
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.test_runs import PROBLEM_JSON, finished
from tests.test_save_checks import ENTITY, PROBE

# --- Which comparisons the SQL converts ------------------------------------------------------


def compared(where: str, dialect: str = "tsql") -> list[tuple[str, str, Conversion | None]]:
    found = comparisons(f"SELECT id FROM ledger_entries WHERE {where}", dialect)
    return [(c.parameter, c.column, c.conversion) for c in found]


DROPS, SETS = Conversion.DROPS, Conversion.SETS


@pytest.mark.parametrize(
    ("where", "conversion"),
    [
        # What each matched on a real SQL Server 2025, natively: see test_adapter_mssql.py.
        ("posted_at = :at", None),
        ("posted_at = SWITCHOFFSET(:at, '+04:00')", None),
        ("posted_at >= DATEADD(hour, -1, :at)", None),  # DATEADD keeps the type
        ("posted_at = CAST(:at AS datetimeoffset)", None),  # the server picks +00:00
        ("posted_at = CONVERT(datetimeoffset, :at)", None),
        ("posted_at = CAST(:at AS datetime2)", DROPS),
        ("posted_at = CONVERT(datetime2, :at)", DROPS),
        ("posted_at = TRY_CONVERT(datetime2(3), :at)", DROPS),
        ("posted_at = CAST(:at AT TIME ZONE 'Arabian Standard Time' AS datetime2)", DROPS),
        # A value without an offset gets the zone's; one with an offset keeps one, so not DROPS.
        ("posted_at = :at AT TIME ZONE 'Arabian Standard Time'", SETS),
        ("posted_at = TODATETIMEOFFSET(:at, '+04:00')", SETS),
        ("posted_at = TODATETIMEOFFSET(:at, 240)", SETS),
        # The outermost conversion decides.
        ("posted_at = CAST(TODATETIMEOFFSET(:at, '+04:00') AS datetime2)", DROPS),
        ("posted_at = CAST(CAST(:at AS datetime2) AS datetimeoffset)", None),
    ],
)
def test_what_each_sql_server_conversion_does_to_the_offset(
    where: str, conversion: Conversion | None
) -> None:
    assert compared(where) == [("at", "posted_at", conversion)]


@pytest.mark.parametrize(
    ("where", "conversion"),
    [
        ("posted_at = :at", None),
        ("posted_at = CAST(:at AS TIMESTAMP WITH TIME ZONE)", None),
        ("posted_at >= :at - INTERVAL '1' DAY", None),
        ("posted_at = CAST(:at AS TIMESTAMP)", DROPS),
        ("posted_at = SYS_EXTRACT_UTC(:at)", DROPS),
        ("posted_at = FROM_TZ(:at, '+04:00')", SETS),
        ("posted_at = FROM_TZ(CAST(:at AS TIMESTAMP), '+04:00')", SETS),
        # Oracle reads a plain TIMESTAMP in the session's time zone first: not the person's choice.
        ("posted_at = :at AT TIME ZONE 'Asia/Dubai'", None),
    ],
)
def test_what_each_oracle_conversion_does_to_the_offset(
    where: str, conversion: Conversion | None
) -> None:
    assert compared(where, "oracle") == [("at", "posted_at", conversion)]


def test_every_kind_of_comparison_is_found_on_either_side() -> None:
    found = comparisons(
        "SELECT e.id FROM ledger_entries e WHERE :since <= e.posted_at "
        "AND e.posted_at BETWEEN :a AND :b AND e.txn_id IN (:t1, :t2) AND e.amount <> :amount",
        "tsql",
    )
    assert set(found) == {
        Comparison("since", "ledger_entries", "posted_at", None),
        Comparison("a", "ledger_entries", "posted_at", None),
        Comparison("b", "ledger_entries", "posted_at", None),
        Comparison("t1", "ledger_entries", "txn_id", None),
        Comparison("t2", "ledger_entries", "txn_id", None),
        Comparison("amount", "ledger_entries", "amount", None),
    }
    assert len(found) == 6


def test_a_column_the_sql_converts_itself_is_left_out() -> None:
    # Converting the column decides its type: TODATETIMEOFFSET(at, '+04:00') matched the
    # column's own clock on the real server.
    assert compared("TODATETIMEOFFSET(posted_at, '+04:00') = :at") == []
    assert compared("posted_at AT TIME ZONE 'Arabian Standard Time' = :at") == []


# --- Types and typed values -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column_type", "offset"),
    [
        ("datetimeoffset", True),
        ("timestamp(6) with time zone", True),
        ("timestamp(6) with local time zone", True),
        ("datetime2", False),
        ("datetime", False),
        ("smalldatetime", False),
        ("date", False),
        ("timestamp(6)", False),
        ("datetime(6)", False),
        ("varchar(20)", None),
        ("number(18,2)", None),
    ],
)
def test_has_offset_reads_each_databases_type_names(column_type: str, offset: bool | None) -> None:
    assert has_offset(column_type) is offset


@pytest.mark.parametrize(
    ("value", "typed"),
    [
        ("2026-11-07T14:00+04:00", TypedTime("+04:00")),
        ("2026-11-07 14:00:00 +0400", TypedTime("+0400")),
        ("2026-11-07T14:00:00.123-05:30", TypedTime("-05:30")),
        ("2026-11-07T10:00:00Z", TypedTime("Z")),
        ("2026-11-07T14:00", TypedTime(None)),
        ("2026-11-07 14:00:00.5", TypedTime(None)),
        ("2026-11-07", None),
        ("tp_91f2c7", None),
    ],
)
def test_typed_time_finds_a_time_and_the_offset_typed(value: str, typed: TypedTime | None) -> None:
    assert typed_time(value) == typed


def snapshot(connector: str, table: str, columns: dict[str, str], **at: Any) -> SchemaSnapshot:
    return SchemaSnapshot(
        application="wallet",
        connector=connector,
        environment=at.get("environment", "prod"),
        imported_at=at.get("imported_at", datetime(2026, 11, 7, tzinfo=UTC)),
        tables=[
            TableSchema(
                table=table,
                columns=[ColumnSchema(name=n, type=t, nullable=False) for n, t in columns.items()],
            )
        ],
    )


def test_the_newest_import_of_a_connector_gives_the_type() -> None:
    old = snapshot("ledger_db", "LEDGER_ENTRIES", {"posted_at": "datetime2"})
    new = snapshot(
        "ledger_db",
        "ledger_entries",
        {"Posted_At": "DATETIMEOFFSET"},
        imported_at=datetime(2026, 11, 8, tzinfo=UTC),
    )
    types = column_types([new, old])
    assert types.of("ledger_db", "Ledger_Entries", "POSTED_AT") == "datetimeoffset"
    assert types.imported == {"ledger_db"}


# --- The save check ---------------------------------------------------------------------------

LEDGER = (
    "SELECT account, direction, amount, posted_at\n"
    "      FROM ledger_entries WHERE txn_id = :ledger_txn_id"
)
assert LEDGER in ENTITY
# The ledger rows from the top-up's own time on: :created_at is the record's created_at.
LEDGER_SINCE = (
    "SELECT account, direction, amount, {posted}\n"
    "      FROM ledger_entries WHERE posted_at >= {since}"
)
# Oracle cannot be read a TIMESTAMP WITH TIME ZONE column as it is: its UTC instant instead.
POSTED_UTC = "SYS_EXTRACT_UTC(posted_at) AS posted_at"


def ledger_since(since: str, posted: str = "posted_at") -> str:
    lookup = LEDGER_SINCE.format(since=since, posted=posted)
    return ENTITY.replace(LEDGER, lookup).replace(
        "key_from: record.ledger_txn_id", "key_from: record.created_at"
    )


WALLET_OFFSET = snapshot("wallet_db", "topups", {"created_at": "datetimeoffset"})
# The example's ledger_db is Oracle.
LEDGER_LOCAL = snapshot("ledger_db", "LEDGER_ENTRIES", {"POSTED_AT": "timestamp(6)"})


def offset_checks_of(setup: Setup, entity: str, *snapshots: SchemaSnapshot) -> list[SaveCheck]:
    report = check_draft(
        setup.applications["wallet"], PROBE, entity, columns=column_types(snapshots)
    )
    return [c for c in report.checks if c.check == TIME_OFFSET]


def ledger_on_sql_server(setup: Setup) -> None:
    app = setup.applications["wallet"]
    sqlserver = app.connectors["ledger_db"].model_copy(update={"type": "sqlserver"})
    connectors = {**app.connectors, "ledger_db": sqlserver}
    setup.applications["wallet"] = replace(app, connectors=connectors)


def test_a_time_with_an_offset_against_a_column_without_one_cannot_be_saved(setup: Setup) -> None:
    # A SQL Server datetimeoffset, read from wallet_db, against Oracle's plain TIMESTAMP.
    [check] = offset_checks_of(setup, ledger_since(":created_at"), WALLET_OFFSET, LEDGER_LOCAL)
    assert (check.status, check.location) == (CheckStatus.FAIL, "ledger")
    assert check.message == (
        "ledger_entries.posted_at is timestamp(6), with no offset, but :created_at comes from "
        "record.created_at, a datetimeoffset. Oracle reads the column in the session's time zone, "
        "so the value can match rows hours off. ProbeOwl cannot know whether the column holds "
        "local time or UTC: convert the value explicitly, for example "
        "CAST(:created_at AT TIME ZONE '<the column's zone>' AS TIMESTAMP) for the column's "
        "local time, or SYS_EXTRACT_UTC(:created_at) when the column holds UTC"
    )


@pytest.mark.parametrize(
    "since",
    [
        "CAST(:created_at AT TIME ZONE '+04:00' AS TIMESTAMP)",
        "CAST(:created_at AS TIMESTAMP)",
        "SYS_EXTRACT_UTC(:created_at)",
    ],
)
def test_an_oracle_lookup_that_converts_the_value_passes(setup: Setup, since: str) -> None:
    [check] = offset_checks_of(setup, ledger_since(since), WALLET_OFFSET, LEDGER_LOCAL)
    assert check.status is CheckStatus.PASS


def test_on_sql_server_the_message_says_how_sql_server_compares(setup: Setup) -> None:
    ledger_on_sql_server(setup)
    local = snapshot("ledger_db", "ledger_entries", {"posted_at": "datetime2"})
    [check] = offset_checks_of(setup, ledger_since(":created_at"), WALLET_OFFSET, local)
    assert check.status is CheckStatus.FAIL
    assert check.message == (
        "ledger_entries.posted_at is datetime2, with no offset, but :created_at comes from "
        "record.created_at, a datetimeoffset. SQL Server compares them in UTC, so a +04:00 value "
        "matches rows 4 hours off when the column holds local time. ProbeOwl cannot know whether "
        "the column holds local time or UTC: convert the value explicitly, for example "
        "CAST(:created_at AS datetime2) for the value's own clock time, or "
        "CAST(:created_at AT TIME ZONE '<the column's zone>' AS datetime2); AT TIME ZONE alone "
        "keeps the offset, so it is not enough"
    )


@pytest.mark.parametrize(
    ("since", "status"),
    [
        ("CAST(:created_at AS datetime2)", CheckStatus.PASS),
        ("CAST(:created_at AT TIME ZONE 'Arabian Standard Time' AS datetime2)", CheckStatus.PASS),
        (":created_at AT TIME ZONE 'Arabian Standard Time'", CheckStatus.FAIL),
    ],
)
def test_on_sql_server_only_an_explicit_conversion_passes(
    setup: Setup, since: str, status: CheckStatus
) -> None:
    ledger_on_sql_server(setup)
    local = snapshot("ledger_db", "ledger_entries", {"posted_at": "datetime2"})
    [check] = offset_checks_of(setup, ledger_since(since), WALLET_OFFSET, local)
    assert check.status is status


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("datetime2", "timestamp(6)"),
        ("datetimeoffset", "timestamp(6) with time zone"),
        ("datetimeoffset", "varchar2(30)"),
    ],
)
def test_matching_types_pass(setup: Setup, source: str, target: str) -> None:
    # Both with an offset, both without, or not a time at all.
    [check] = offset_checks_of(
        setup,
        ledger_since(":created_at", POSTED_UTC),
        snapshot("wallet_db", "topups", {"created_at": source}),
        snapshot("ledger_db", "ledger_entries", {"posted_at": target}),
    )
    assert check.status is CheckStatus.PASS


def test_a_time_column_on_a_connector_with_no_import_warns(setup: Setup) -> None:
    # posted_at is the ledger's time column in the catalog, and ledger_db has no import.
    [check] = offset_checks_of(setup, ledger_since(":created_at"), WALLET_OFFSET)
    assert (check.status, check.message) == (
        CheckStatus.WARN,
        "ledger_db's schema is not imported, so its time columns are not checked for offsets: "
        "import it on the Tables page",
    )


def test_a_key_that_is_not_a_time_column_needs_no_import(setup: Setup) -> None:
    # The example's ledger is keyed by txn_id, which the catalog does not mark as time; only the
    # healthy sample compares times, on wallet_db, which is imported here.
    [check] = offset_checks_of(setup, ENTITY, WALLET_OFFSET)
    assert check.status is CheckStatus.PASS


def test_the_check_is_left_out_without_a_store(setup: Setup) -> None:
    report = check_draft(setup.applications["wallet"], PROBE, ledger_since(":created_at"))
    assert not [c for c in report.checks if c.check == TIME_OFFSET]


def store_snapshots(setup: Setup, *snapshots: SchemaSnapshot) -> None:
    with setup.store.begin() as conn:
        for s in snapshots:
            save_snapshot(conn, LOCAL_WORKSPACE, s)


def test_the_checks_route_reads_the_imported_types(setup: Setup) -> None:
    store_snapshots(setup, WALLET_OFFSET, LEDGER_LOCAL)
    response = setup.client().post(
        "/api/applications/wallet/probes/checks",
        json={"probe": PROBE, "entity": ledger_since(":created_at")},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["can_save"] is False
    assert [c["location"] for c in report["checks"] if c["status"] == "fail"] == ["ledger"]


# --- A value typed with an offset, refused before the run starts -------------------------------

# An identifier for the top-up's own time, looked up on the record.
BY_TIME = ENTITY.replace(
    "identifiers:\n",
    "identifiers:\n"
    "  created_at: { pattern: '^[0-9]{4}-.*$', via: record.created_at, unique: false }\n",
)
assert BY_TIME != ENTITY


def draft_run(setup: Setup, value: str) -> Any:
    return setup.client().post(
        "/api/applications/wallet/probes/test-runs",
        json={
            "probe": PROBE,
            "entity": BY_TIME,
            "environment": "prod",
            "value": value,
            "investigation": {"reason": "incident", "reference": "WALLET-482"},
        },
    )


def test_a_value_typed_with_an_offset_against_a_column_without_one_is_refused(
    setup: Setup,
) -> None:
    store_snapshots(setup, snapshot("wallet_db", "topups", {"created_at": "datetime2"}))
    response = draft_run(setup, "2026-11-07T14:00+04:00")
    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    problem = response.json()
    assert problem["detail"] == (
        "topups.created_at is datetime2: this column stores time without an offset"
    )
    assert problem["errors"] == [
        {
            "field": "value",
            "message": "this column stores time without an offset; enter it without +04:00, "
            "in the column's own time",
        }
    ]
    assert setup.fakes["wallet_db"].calls == []


def test_the_offset_named_is_the_one_typed(setup: Setup) -> None:
    store_snapshots(setup, snapshot("wallet_db", "topups", {"created_at": "datetime2"}))
    problem = draft_run(setup, "2026-11-07T10:00:00Z").json()
    assert "enter it without Z," in problem["errors"][0]["message"]


def test_a_value_with_an_offset_runs_against_a_column_with_one(setup: Setup) -> None:
    store_snapshots(setup, WALLET_OFFSET)
    response = draft_run(setup, "2026-11-07T14:00+04:00")
    assert response.status_code == 202, response.text
    finished(setup.client(), response.json()["id"])


def test_a_value_without_an_offset_runs(setup: Setup) -> None:
    store_snapshots(setup, snapshot("wallet_db", "topups", {"created_at": "datetime2"}))
    response = draft_run(setup, "2026-11-07T14:00")
    assert response.status_code == 202, response.text


def test_with_no_import_a_value_with_an_offset_is_refused_rather_than_guessed(
    setup: Setup,
) -> None:
    response = draft_run(setup, "2026-11-07T14:00+04:00")
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["detail"] == (
        "wallet_db's schema is not imported in prod, so ProbeOwl cannot tell whether "
        "topups.created_at stores time with an offset"
    )
    assert problem["errors"][0]["message"] == (
        "enter it without +04:00, in the column's own time, or import wallet_db's schema first"
    )


def test_an_import_in_another_environment_does_not_count(setup: Setup) -> None:
    store_snapshots(
        setup, snapshot("wallet_db", "topups", {"created_at": "datetimeoffset"}, environment="uat")
    )
    assert draft_run(setup, "2026-11-07T14:00+04:00").status_code == 422


def test_an_empty_types_table_knows_nothing() -> None:
    assert ColumnTypes({}, frozenset()).of("wallet_db", "topups", "created_at") is None


def test_the_rule_agrees_with_what_a_real_sql_server_matched() -> None:
    # A comparison the check refuses is exactly one that matched the row 4 hours off.
    from tests.test_adapter_mssql import OFFSET_FORMS

    for where, matched in OFFSET_FORMS.items():
        found = comparisons(f"SELECT id FROM t WHERE {where}", "tsql")
        refused = any(c.conversion is not Conversion.DROPS for c in found)
        assert refused == (matched == "utc_clock"), where


def test_the_reverse_rule_agrees_with_what_a_real_sql_server_matched() -> None:
    # A value without an offset: a comparison the check refuses is one read as UTC.
    from tests.test_adapter_mssql import REVERSE_FORMS

    for where, matched in REVERSE_FORMS.items():
        found = comparisons(f"SELECT id FROM t WHERE {where}", "tsql")
        refused = any(c.conversion is not Conversion.SETS for c in found)
        assert refused == (matched == "utc"), where


# --- The other way round: a time without an offset against a column with one -----------------

LEDGER_OFFSET = snapshot(
    "ledger_db", "LEDGER_ENTRIES", {"POSTED_AT": "timestamp(6) with time zone"}
)
WALLET_LOCAL = snapshot("wallet_db", "topups", {"created_at": "datetime2"})


def test_a_time_without_an_offset_against_a_column_with_one_cannot_be_saved(setup: Setup) -> None:
    # A SQL Server datetime2, read from wallet_db, against Oracle's TIMESTAMP WITH TIME ZONE.
    lookup = ledger_since(":created_at", POSTED_UTC)
    [check] = offset_checks_of(setup, lookup, WALLET_LOCAL, LEDGER_OFFSET)
    assert (check.status, check.location) == (CheckStatus.FAIL, "ledger")
    assert check.message == (
        "ledger_entries.posted_at is timestamp(6) with time zone, with an offset, but :created_at "
        "comes from record.created_at, a datetime2, with none. Oracle reads the value in the "
        "session's time zone, so it can match rows hours off. ProbeOwl cannot know which zone the "
        "value is in: give it its offset explicitly, for example FROM_TZ(:created_at, '+04:00') "
        "with the value's own offset"
    )


def test_an_oracle_lookup_that_gives_the_value_its_offset_passes(setup: Setup) -> None:
    since = "FROM_TZ(:created_at, '+04:00')"
    lookup = ledger_since(since, POSTED_UTC)
    [check] = offset_checks_of(setup, lookup, WALLET_LOCAL, LEDGER_OFFSET)
    assert check.status is CheckStatus.PASS


def test_on_sql_server_a_time_without_an_offset_says_it_is_read_as_utc(setup: Setup) -> None:
    ledger_on_sql_server(setup)
    offset = snapshot("ledger_db", "ledger_entries", {"posted_at": "datetimeoffset"})
    [check] = offset_checks_of(setup, ledger_since(":created_at"), WALLET_LOCAL, offset)
    assert check.status is CheckStatus.FAIL
    assert check.message == (
        "ledger_entries.posted_at is datetimeoffset, with an offset, but :created_at comes from "
        "record.created_at, a datetime2, with none. SQL Server reads the value as UTC, so a value "
        "in local time matches rows hours off. ProbeOwl cannot know which zone the value is in: "
        "give it its offset explicitly, for example TODATETIMEOFFSET(:created_at, '+04:00') with "
        "the value's own offset, or :created_at AT TIME ZONE '<the value's zone>'"
    )


@pytest.mark.parametrize(
    ("since", "status"),
    [
        ("TODATETIMEOFFSET(:created_at, '+04:00')", CheckStatus.PASS),
        (":created_at AT TIME ZONE 'Arabian Standard Time'", CheckStatus.PASS),
        ("CAST(:created_at AS datetimeoffset)", CheckStatus.FAIL),
        ("SWITCHOFFSET(:created_at, '+04:00')", CheckStatus.FAIL),
    ],
)
def test_on_sql_server_only_giving_the_value_an_offset_passes(
    setup: Setup, since: str, status: CheckStatus
) -> None:
    ledger_on_sql_server(setup)
    offset = snapshot("ledger_db", "ledger_entries", {"posted_at": "datetimeoffset"})
    [check] = offset_checks_of(setup, ledger_since(since), WALLET_LOCAL, offset)
    assert check.status is status


def test_a_time_typed_without_an_offset_against_a_column_with_one_is_refused(
    setup: Setup,
) -> None:
    store_snapshots(setup, WALLET_OFFSET)
    response = draft_run(setup, "2026-11-07T14:00")
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["detail"] == (
        "topups.created_at is datetimeoffset: this column stores time with an offset"
    )
    assert problem["errors"] == [
        {
            "field": "value",
            "message": "this column stores time with an offset; enter the time with its offset, "
            "such as 2026-11-07T14:00+04:00",
        }
    ]
    assert setup.fakes["wallet_db"].calls == []


def test_with_no_import_a_time_without_an_offset_is_refused_too(setup: Setup) -> None:
    problem = draft_run(setup, "2026-11-07T14:00").json()
    assert problem["errors"][0]["message"] == (
        "enter the time with its offset, such as 2026-11-07T14:00+04:00, or import wallet_db's "
        "schema first"
    )


def test_owl_check_names_each_connector_it_cannot_check(setup: Setup) -> None:
    assert unchecked_without_imports(setup.applications["wallet"]) == ["wallet_db"]
    assert unchecked_without_imports(setup.applications["card_access"]) == []


# --- Oracle: a TIMESTAMP WITH TIME ZONE column is never read as it is ------------------------


def test_selecting_a_time_with_time_zone_column_on_oracle_cannot_be_saved(setup: Setup) -> None:
    # Thin mode reads it as its clock time with no offset, so the adapter refuses it at run time.
    [check] = offset_checks_of(setup, ledger_since(":created_at"), WALLET_LOCAL, LEDGER_OFFSET)[1:]
    assert (check.status, check.location) == (CheckStatus.FAIL, "ledger")
    assert check.message == (
        "posted_at is TIMESTAMP WITH TIME ZONE, which ProbeOwl cannot read with its offset: "
        "select SYS_EXTRACT_UTC(posted_at) for its UTC instant, or "
        "TO_CHAR(posted_at, 'YYYY-MM-DD HH24:MI:SS TZH:TZM')"
    )


def test_select_star_names_each_time_with_time_zone_column(setup: Setup) -> None:
    entity = ENTITY.replace(LEDGER, "SELECT * FROM ledger_entries WHERE txn_id = :ledger_txn_id")
    columns = {"txn_id": "varchar2(20)", "posted_at": "timestamp(6) with time zone"}
    checks = offset_checks_of(
        setup, entity, WALLET_OFFSET, snapshot("ledger_db", "LEDGER_ENTRIES", columns)
    )
    assert [c.message.split(" ", 1)[0] for c in checks] == ["posted_at"]


@pytest.mark.parametrize(
    ("posted", "kind"),
    [
        (POSTED_UTC, "timestamp(6) with time zone"),
        (
            "TO_CHAR(posted_at, 'YYYY-MM-DD HH24:MI:SS TZH:TZM') AS posted_at",
            "timestamp(6) with time zone",
        ),
        # Local time zone values come back in the session's zone, which ProbeOwl now sets.
        ("posted_at", "timestamp(6) with local time zone"),
        ("posted_at", "timestamp(6)"),
    ],
)
def test_reading_it_through_a_conversion_or_another_type_passes(
    setup: Setup, posted: str, kind: str
) -> None:
    entity = ENTITY.replace(
        LEDGER, f"SELECT account, {posted} FROM ledger_entries WHERE txn_id = :ledger_txn_id"
    )
    columns = {"posted_at": kind, "account": "varchar2(30)"}
    [check] = offset_checks_of(
        setup, entity, WALLET_OFFSET, snapshot("ledger_db", "LEDGER_ENTRIES", columns)
    )
    assert check.status is CheckStatus.PASS
