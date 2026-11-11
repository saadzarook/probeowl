# Author: @SaadZarook
"""Label suggestions, probe usage, drift per environment, and the catalog review."""

from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from probeowl.catalog.drift import catalog_drift, environment_drift
from probeowl.catalog.review import build_review
from probeowl.catalog.suggest import KeyTable, key_tables, suggest
from probeowl.catalog.usage import tables_used
from probeowl.cli import main as cli
from probeowl.cli import server
from probeowl.model.catalog import ColumnRole, LinkTarget, TableLabel
from probeowl.model.schema import ColumnSchema, IndexSchema, SchemaSnapshot, TableSchema
from tests.conftest import Setup
from tests.test_catalog_import_api import Database, client


def table(
    name: str,
    *columns: str,
    key: tuple[str, ...] = ("id",),
    rows: int | None = 1_000_000,
    indexes: tuple[tuple[str, ...], ...] = (),
) -> TableSchema:
    def column(spec: str) -> ColumnSchema:
        name, _, type_ = spec.partition(":")
        return ColumnSchema(name=name, type=type_ or "varchar(32)", nullable=False)

    return TableSchema(
        schema="dbo",
        table=name,
        columns=[column(c) for c in ("id", *columns)],
        primary_key=list(key),
        indexes=[
            IndexSchema(name=f"ix_{name}_{i}", columns=list(cols), unique=False)
            for i, cols in enumerate(indexes)
        ],
        approx_rows=rows,
    )


# The tables of the design's Table catalog screen, as an import would show them.
TOPUPS = table(
    "topups",
    "user_id",
    "provider_payment_id",
    "ledger_txn_id",
    "status:varchar(16)",
    "amount:decimal(18,2)",
    "created_at:datetime2",
    "payment_method",
    "app_version",
    "customer_email:nvarchar(256)",
    indexes=(("id",), ("created_at",)),
)
WALLET_TABLES = [
    TOPUPS,
    table("wallets", "user_id", "balance:decimal(18,2)"),
    table("topup_status_history", "topup_id", "status", "changed_at:datetime2"),
    table("provider_webhooks", "payment_id", "received_at:datetime2", indexes=(("received_at",),)),
    table("currencies", "code:char(3)", "name", rows=42),
    table("user_provider_ids", "user_id", "provider_user_id"),
    table(
        "outbox_events",
        "aggregate_id",
        "status",
        "created_at:datetime2",
        indexes=(("created_at",),),
    ),
]


@pytest.mark.parametrize(
    ("schema", "label", "reason"),
    [
        (TOPUPS, TableLabel.TRANSACTION, "created_at and status columns"),
        (WALLET_TABLES[1], TableLabel.MASTER, "no time column: keyed lookups of current records"),
        (WALLET_TABLES[2], TableLabel.AUDIT, "name ends in _history"),
        (WALLET_TABLES[3], TableLabel.LOG, "name says log, webhook, delivery or notification"),
        (WALLET_TABLES[4], TableLabel.REFERENCE, "about 42 rows and no time column"),
        (
            WALLET_TABLES[5],
            TableLabel.EXTERNAL_MAPPING,
            "holds another system's key (provider_user_id)",
        ),
        (WALLET_TABLES[6], TableLabel.OUTBOX, "name says outbox or queue"),
    ],
    ids=lambda v: getattr(v, "table", None) or str(v),
)
def test_every_table_gets_a_label_suggestion_with_its_reason(
    schema: TableSchema, label: TableLabel, reason: str
) -> None:
    found = suggest(schema)
    assert found.label is label
    assert found.reasons == [reason]


@pytest.mark.parametrize(
    ("schema", "reason"),
    [
        # The newcomer test's case: a profile has a created_at and a status, and is master.
        (
            table("visitor_profile", "email", "status", "created_at:datetime"),
            "name says visitor: one current record per subject, whatever its created_at column",
        ),
        (
            table("customers", "name", "state", "created_on:date"),
            "name says customers: one current record per subject, whatever its created_on column",
        ),
        (
            table("user_accounts", "user_id", "balance"),
            "name says user: one current record per subject",
        ),
    ],
    ids=lambda v: getattr(v, "table", None) or "",
)
def test_a_table_named_for_a_subject_is_master_even_with_a_time_and_status(
    schema: TableSchema, reason: str
) -> None:
    found = suggest(schema)
    assert (found.label, found.reasons) == (TableLabel.MASTER, [reason])


@pytest.mark.parametrize("name", ["orders", "payments", "topups", "card_transactions", "bookings"])
def test_tables_of_records_that_pile_up_stay_transactions(name: str) -> None:
    found = suggest(table(name, "customer_id", "status", "created_at:datetime2"))
    assert found.label is TableLabel.TRANSACTION


def test_suggested_column_roles() -> None:
    roles = suggest(TOPUPS).columns
    assert roles["id"] == [ColumnRole.PRIMARY_KEY]
    assert roles["created_at"] == [ColumnRole.TIME]
    assert roles["status"] == [ColumnRole.STATUS]
    assert roles["customer_email"] == [ColumnRole.PII]
    assert roles["provider_payment_id"] == [ColumnRole.EXTERNAL_KEY]
    assert "amount" not in roles


# The retest's tables: cards.visitor_profile_id holds visitor_profile.id.
VISITORS = table("visitor_profile", "email", "status", "created_at:datetime")
CARDS = table("cards", "visitor_profile_id", "status", "valid_from:date", key=("card_id",))


def test_a_column_named_for_another_tables_key_is_suggested_as_a_link_to_it() -> None:
    keys = key_tables([("access_db", VISITORS), ("access_db", CARDS)])
    found = suggest(CARDS, "access_db", keys)
    assert found.columns["visitor_profile_id"] == [ColumnRole.LINK]
    assert found.links == {
        "visitor_profile_id": LinkTarget(
            connector="access_db", table="visitor_profile", column="id"
        )
    }
    # The table's own key is not a link to itself.
    assert suggest(VISITORS, "access_db", keys).links == {}


def test_a_singular_name_links_too_and_a_composite_key_is_never_a_target() -> None:
    history = table("topup_status_history", "topup_id", "status", "changed_at:datetime2")
    ledger = table("ledger_entries", "txn_id", "direction", key=("txn_id", "direction"))
    postings = table("postings", "ledger_entries_txn_id", "ledger_entry_txn_id")
    keys = key_tables([("wallet_db", TOPUPS), ("wallet_db", history), ("ledger_db", ledger)])
    assert suggest(history, "wallet_db", keys).links == {
        "topup_id": LinkTarget(connector="wallet_db", table="topups", column="id")
    }
    assert suggest(postings, "ledger_db", keys).links == {}


def test_a_table_on_the_same_connector_wins_and_two_elsewhere_are_a_guess() -> None:
    orders = table("orders", "customer_id", "status", "created_at:datetime2")
    customers = KeyTable("crm_db", "customers", "id")
    assert suggest(
        orders, "shop_db", [customers, KeyTable("shop_db", "customers", "id")]
    ).links == {"customer_id": LinkTarget(connector="shop_db", table="customers", column="id")}
    assert (
        suggest(orders, "shop_db", [customers, KeyTable("erp_db", "customers", "id")]).links == {}
    )
    assert suggest(orders, "shop_db", [customers]).links == {
        "customer_id": LinkTarget(connector="crm_db", table="customers", column="id")
    }


def test_another_systems_key_is_not_suggested_as_a_link() -> None:
    payments = table("payments", "provider_payment_id")
    keys = [
        KeyTable("wallet_db", "provider_payments", "id"),
        KeyTable("wallet_db", "provider_payment", "id"),
    ]
    found = suggest(payments, "wallet_db", keys)
    assert found.links == {}
    assert found.columns["provider_payment_id"] == [ColumnRole.EXTERNAL_KEY]


def test_without_a_known_name_the_first_date_column_is_the_time_column() -> None:
    found = suggest(table("orders", "status", "placed:datetime2"))
    assert found.columns["placed"] == [ColumnRole.TIME]
    assert found.label is TableLabel.TRANSACTION


# --- Usage ------------------------------------------------------------------------------------


def test_tables_used_by_probes_come_from_their_entities_lookups(setup: Setup) -> None:
    wallet = tables_used(setup.applications["wallet"])
    assert wallet[("wallet_db", "topups")] == ["topup_completed"]
    assert wallet[("wallet_db", "provider_webhooks")] == ["topup_completed"]
    assert wallet[("ledger_db", "ledger_entries")] == ["topup_completed"]
    cards = tables_used(setup.applications["card_access"])
    assert set(cards) == {("access_db", "visitor_profile"), ("access_db", "cards")}


# --- Drift ------------------------------------------------------------------------------------


def test_the_catalog_checked_against_a_live_table(setup: Setup) -> None:
    catalog = setup.applications["wallet"].catalog
    assert catalog is not None
    [entry] = [t for t in catalog.tables if t.table == "topups"]
    live = TOPUPS.model_copy(update={"columns": TOPUPS.columns[:-1], "indexes": []})
    messages = [d.message for d in catalog_drift(entry, "uat", live)]
    assert messages == [
        "column customer_email is not in the live schema",
        "created_at is marked indexed, but no index starts with it",
    ]
    assert [d.message for d in catalog_drift(entry, "uat", None)] == ["not in the live schema"]
    assert catalog_drift(entry, "prod", TOPUPS) == []


def test_environments_compared_with_the_first() -> None:
    uat = TOPUPS.model_copy(
        update={
            "columns": [
                c.model_copy(update={"type": "varchar(20)"}) if c.name == "status" else c
                for c in TOPUPS.columns
                if c.name != "customer_email"
            ]
            + [ColumnSchema(name="note", type="varchar(200)", nullable=True)]
        }
    )
    messages = [d.message for d in environment_drift({"prod": TOPUPS, "uat": uat})]
    assert messages == [
        "status is varchar(20) here but varchar(16) in prod",
        "column customer_email is missing; prod has it",
        "column note is not in prod",
    ]


# --- The review -------------------------------------------------------------------------------


def snapshot(
    environment: str, tables: list[TableSchema], connector: str = "wallet_db"
) -> SchemaSnapshot:
    return SchemaSnapshot(
        application="wallet",
        connector=connector,
        environment=environment,
        imported_at=datetime(2026, 11, 2, tzinfo=UTC),
        tables=tables,
    )


def test_the_review_brings_catalog_imports_and_usage_together(setup: Setup) -> None:
    review = build_review(setup.applications["wallet"], [snapshot("prod", WALLET_TABLES)])
    by_name = {t.table: t for t in review.tables}
    topups = by_name["topups"]
    assert (topups.label, topups.suggestion and topups.suggestion.label) == (
        TableLabel.TRANSACTION,
        TableLabel.TRANSACTION,
    )
    assert topups.imported_in == ["prod"] and topups.used_by == ["topup_completed"]
    assert topups.db_schema == "dbo"
    # In an import, not in catalog.yaml: suggested, waiting for a person.
    assert by_name["wallets"].label is None
    assert by_name["wallets"].suggestion is not None
    # In catalog.yaml, connector imported, table missing there: drift.
    assert [d.message for d in by_name["ledger_entries"].drift] == []  # ledger_db not imported
    # Every imported table has a suggestion: the M3 exit check.
    assert all(by_name[t.table].suggestion for t in WALLET_TABLES)
    # wallets, the status history, currencies and provider ids are not in catalog.yaml yet.
    assert review.unconfirmed == 4
    assert review.drifted == 0


def test_the_review_suggests_links_across_the_applications_imports(setup: Setup) -> None:
    history = table("topup_status_history", "topup_id", "status", "changed_at:datetime2")
    review = build_review(
        setup.applications["wallet"],
        [snapshot("prod", [TOPUPS, history]), snapshot("prod", [VISITORS], "ledger_db")],
    )
    by_name = {t.table: t for t in review.tables}
    suggestion = by_name["topup_status_history"].suggestion
    assert suggestion is not None
    assert suggestion.links == {
        "topup_id": LinkTarget(connector="wallet_db", table="topups", column="id")
    }
    assert ColumnRole.LINK in suggestion.columns["topup_id"]


def test_a_catalog_table_missing_from_an_imported_connector_is_drift(setup: Setup) -> None:
    review = build_review(setup.applications["wallet"], [snapshot("uat", WALLET_TABLES[1:])])
    [topups] = [t for t in review.tables if t.table == "topups"]
    assert [(d.environment, d.message) for d in topups.drift] == [("uat", "not in the live schema")]
    assert topups.suggestion is None and topups.imported_in == []
    assert review.drifted == 1


# --- API and CLI ------------------------------------------------------------------------------


def test_the_review_route_reads_the_latest_imports(setup: Setup) -> None:
    api = client(setup, Database(WALLET_TABLES))
    assert (
        api.post(
            "/api/applications/wallet/catalog/imports",
            json={"connector": "wallet_db", "environment": "prod"},
        ).status_code
        == 201
    )
    review = api.get("/api/applications/wallet/catalog/review").json()
    [wallets] = [t for t in review["tables"] if t["table"] == "wallets"]
    assert wallets["label"] is None
    assert wallets["suggestion"]["label"] == "master"
    assert wallets["imported_in"] == ["prod"]
    assert review["unconfirmed"] == 4


def test_owl_catalog_import_prints_a_suggestion_for_every_table(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = client(setup, Database(WALLET_TABLES))
    monkeypatch.setattr(server, "client", lambda url: api)
    out = (
        CliRunner()
        .invoke(
            cli.app, ["catalog", "import", "wallet_db", "--application", "wallet", "--env", "prod"]
        )
        .output
    )
    assert "    confirmed: transaction" in out  # topups: catalog.yaml agrees
    assert (
        "    suggested: master (no time column: keyed lookups of current records); "
        "confirm it on the Tables screen"
    ) in out
    assert out.count("    suggested: ") + out.count("    confirmed: ") == len(WALLET_TABLES)
    # topup_status_history.topup_id is named for topups' key, and the history is not confirmed yet.
    assert "    suggested link: topup_id to topups.id" in out


def test_one_row_is_said_in_the_singular() -> None:
    assert suggest(table("settings", "name", rows=1)).reasons == ["about 1 row and no time column"]
