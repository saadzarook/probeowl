# Author: @SaadZarook
"""Schema import without a server: assembling catalog rows, and each dialect's mapping."""

from typing import Any

import pytest

from probeowl.connectors.mariadb import MariaDbAdapter
from probeowl.connectors.oracle import OracleAdapter
from probeowl.connectors.schema import MAX_TABLES, ColumnRow, IndexColumnRow, assemble
from probeowl.connectors.sql import SqlAdapter
from probeowl.connectors.sqlserver import SqlServerAdapter
from probeowl.model import SqlBinding

BINDING = SqlBinding(host="db-ro", database="wallet", user="probeowl_ro", secret="env:PW")


def test_rows_become_tables_with_keys_and_indexes_in_order() -> None:
    columns = [
        ColumnRow(("dbo", "topups"), "id", "varchar(20)", False),
        ColumnRow(("dbo", "topups"), "status", "varchar(16)", False),
        ColumnRow(("dbo", "audit"), "id", "int", False),
    ]
    index_columns = [
        IndexColumnRow(("dbo", "topups"), "pk_topups", True, True, "id"),
        IndexColumnRow(("dbo", "topups"), "ix_status_created", False, False, "status"),
        IndexColumnRow(("dbo", "topups"), "ix_status_created", False, False, "created_at"),
    ]
    tables, truncated = assemble(columns, index_columns, {("dbo", "topups"): 1200})
    assert truncated is None
    assert [t.table for t in tables] == ["audit", "topups"]
    topups = tables[1]
    assert topups.db_schema == "dbo"
    assert [c.name for c in topups.columns] == ["id", "status"]
    assert topups.primary_key == ["id"]
    assert [(i.name, i.columns, i.unique) for i in topups.indexes] == [
        ("ix_status_created", ["status", "created_at"], False),
        ("pk_topups", ["id"], True),
    ]
    assert topups.approx_rows == 1200
    assert tables[0].approx_rows is None and tables[0].primary_key == []


def test_a_primary_key_constraint_is_used_when_given() -> None:
    columns = [ColumnRow(("ledger", "entries"), c, "varchar2(20)", False) for c in ("a", "b")]
    tables, _ = assemble(columns, [], {}, {("ledger", "entries"): ["a", "b"]})
    assert tables[0].primary_key == ["a", "b"]


def test_more_tables_than_the_cap_are_cut_and_said() -> None:
    columns = [ColumnRow((None, f"t{i:05d}"), "id", "int", False) for i in range(MAX_TABLES + 5)]
    tables, truncated = assemble(columns, [], {})
    assert len(tables) == MAX_TABLES
    assert truncated == f"imported {MAX_TABLES} of {MAX_TABLES + 5:,} tables"


class Catalog:
    """A connection whose catalog queries answer from rows keyed by a phrase in the SQL."""

    def __init__(self, answers: dict[str, list[tuple[Any, ...]]]):
        self.answers = answers
        self.sent: list[tuple[str, Any]] = []

    def cursor(self) -> Any:
        catalog = self

        class Cursor:
            def __init__(self) -> None:
                self.rows: list[tuple[Any, ...]] = []

            def execute(self, sql: str, parameters: Any = None) -> None:
                catalog.sent.append((sql, parameters))
                [self.rows] = [rows for key, rows in catalog.answers.items() if key in sql]

            def fetchall(self) -> list[tuple[Any, ...]]:
                return self.rows

            def close(self) -> None:
                pass

        return Cursor()


def imported(adapter: SqlAdapter, answers: dict[str, list[tuple[Any, ...]]]) -> Any:
    catalog = Catalog(answers)
    adapter._connection = catalog
    tables, _ = adapter.import_schema()
    return tables, catalog


def test_mariadb_reads_its_own_database_only() -> None:
    tables, catalog = imported(
        MariaDbAdapter(BINDING, "pw"),
        {
            "information_schema.COLUMNS": [
                ("cards", "card_id", "varchar(10)", "NO"),
                ("cards", "valid_to", "date", "YES"),
            ],
            "information_schema.STATISTICS": [
                ("cards", "PRIMARY", 0, "card_id"),
                ("cards", "ix_cards_visitor", 1, "visitor_profile_id"),
            ],
            "TABLE_ROWS": [("cards", 3)],
        },
    )
    [cards] = tables
    assert cards.db_schema is None
    assert [(c.name, c.type, c.nullable) for c in cards.columns] == [
        ("card_id", "varchar(10)", False),
        ("valid_to", "date", True),
    ]
    assert cards.primary_key == ["card_id"]
    assert {i.name: i.unique for i in cards.indexes} == {"PRIMARY": True, "ix_cards_visitor": False}
    assert cards.approx_rows == 3
    assert all("DATABASE()" in sql for sql, _ in catalog.sent)


@pytest.mark.parametrize(
    ("data_type", "length", "precision", "scale", "expected"),
    [
        ("varchar", 20, None, None, "varchar(20)"),
        ("nvarchar", -1, None, None, "nvarchar(max)"),
        ("decimal", None, 18, 2, "decimal(18,2)"),
        ("int", None, 10, 0, "int"),  # precision of an int is not written
        ("datetime2", None, None, None, "datetime2"),
    ],
)
def test_sql_server_writes_types_as_people_do(
    data_type: str, length: int | None, precision: int | None, scale: int | None, expected: str
) -> None:
    tables, _ = imported(
        SqlServerAdapter(BINDING, "pw"),
        {
            "INFORMATION_SCHEMA.COLUMNS": [
                ("dbo", "topups", "x", data_type, length, precision, scale, "NO")
            ],
            "sys.indexes": [("dbo", "topups", "PK_topups", True, True, "x")],
            "sys.partitions": [("dbo", "topups", 1200)],
        },
    )
    assert tables[0].columns[0].type == expected
    assert tables[0].primary_key == ["x"]
    assert tables[0].approx_rows == 1200


def test_oracle_reads_the_binding_schema_and_shows_names_in_lower_case() -> None:
    binding = BINDING.model_copy(update={"db_schema": "ledger"})
    tables, catalog = imported(
        OracleAdapter(binding, "pw"),
        {
            "all_tab_columns": [
                ("LEDGER_ENTRIES", "TXN_ID", "VARCHAR2", 20, None, None, "N"),
                ("LEDGER_ENTRIES", "AMOUNT", "NUMBER", 22, 18, 2, "N"),
                ("LEDGER_ENTRIES", "POSTED_AT", "DATE", 7, None, None, "Y"),
            ],
            "constraint_type = 'P'": [
                ("LEDGER_ENTRIES", "TXN_ID"),
                ("LEDGER_ENTRIES", "DIRECTION"),
            ],
            "all_ind_columns": [("LEDGER_ENTRIES", "IX_POSTED", "NONUNIQUE", "POSTED_AT")],
            "num_rows": [("LEDGER_ENTRIES", None)],
        },
    )
    [entries] = tables
    assert (entries.db_schema, entries.table) == ("ledger", "ledger_entries")
    assert [(c.name, c.type) for c in entries.columns] == [
        ("txn_id", "varchar2(20)"),
        ("amount", "number(18,2)"),
        ("posted_at", "date"),
    ]
    assert entries.primary_key == ["txn_id", "direction"]
    assert [(i.name, i.columns, i.unique) for i in entries.indexes] == [
        ("ix_posted", ["posted_at"], False)
    ]
    assert entries.approx_rows is None  # statistics not gathered
    assert all(parameters == {"owner": "LEDGER"} for _, parameters in catalog.sent)
