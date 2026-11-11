# Author: @SaadZarook
"""Assemble the rows each dialect's catalog views return into tables. Shared by the adapters."""

from collections import defaultdict
from dataclasses import dataclass

from probeowl.model.schema import ColumnSchema, IndexSchema, TableSchema

MAX_TABLES = 1000

# A table, as (schema or owner, name); MariaDB has no schema.
TableKey = tuple[str | None, str]


@dataclass(frozen=True)
class ColumnRow:
    """One column, in table order."""

    table: TableKey
    name: str
    type: str
    nullable: bool


@dataclass(frozen=True)
class IndexColumnRow:
    """One key column of one index, in key order."""

    table: TableKey
    index: str
    unique: bool
    primary: bool
    column: str


def assemble(
    columns: list[ColumnRow],
    index_columns: list[IndexColumnRow],
    approx_rows: dict[TableKey, int | None],
    primary_keys: dict[TableKey, list[str]] | None = None,
) -> tuple[list[TableSchema], str | None]:
    """Tables in name order, at most MAX_TABLES; and why some were left out, if any were.

    primary_keys: for dialects whose primary key is a constraint, not a flagged index.
    """
    by_table: dict[TableKey, list[ColumnSchema]] = defaultdict(list)
    for column in columns:
        by_table[column.table].append(
            ColumnSchema(name=column.name, type=column.type, nullable=column.nullable)
        )
    indexes: dict[TableKey, dict[str, IndexSchema]] = defaultdict(dict)
    primary = dict(primary_keys or {})
    for row in index_columns:
        found = indexes[row.table].get(row.index)
        if found is None:
            indexes[row.table][row.index] = IndexSchema(
                name=row.index, columns=[row.column], unique=row.unique
            )
        else:
            found.columns.append(row.column)
        if row.primary and primary_keys is None:
            primary.setdefault(row.table, []).append(row.column)
    keys = sorted(by_table, key=lambda key: (key[0] or "", key[1]))
    tables = [
        TableSchema(
            schema=key[0],
            table=key[1],
            columns=by_table[key],
            primary_key=primary.get(key, []),
            indexes=sorted(indexes[key].values(), key=lambda index: index.name),
            approx_rows=approx_rows.get(key),
        )
        for key in keys[:MAX_TABLES]
    ]
    # "imported 1000 of 1,240 tables": the cap is reported, never applied silently.
    truncated = f"imported {MAX_TABLES} of {len(keys):,} tables" if len(keys) > MAX_TABLES else None
    return tables, truncated
