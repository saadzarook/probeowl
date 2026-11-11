# Author: @SaadZarook
"""A database's schema as imported from its catalog views: tables, columns, keys, never data."""

from datetime import datetime

from pydantic import Field

from probeowl.model.common import Name, ProbeOwlModel


class ColumnSchema(ProbeOwlModel):
    """One column as the database defines it."""

    name: str
    type: str = Field(description="As the database names it, such as varchar(20) or number(18,2).")
    nullable: bool


class IndexSchema(ProbeOwlModel):
    """One index, its key columns in order."""

    name: str
    columns: list[str] = Field(min_length=1)
    unique: bool


class TableSchema(ProbeOwlModel):
    """One table: its columns, primary key, indexes and an estimate of its rows."""

    db_schema: str | None = Field(
        default=None,
        alias="schema",
        description="SQL Server and Oracle: the schema or owner. MariaDB: none (the database).",
    )
    table: str
    columns: list[ColumnSchema]
    primary_key: list[str] = Field(default_factory=list)
    indexes: list[IndexSchema] = Field(default_factory=list)
    approx_rows: int | None = Field(
        default=None, ge=0, description="The database's own estimate, not a count."
    )


class SchemaSnapshot(ProbeOwlModel):
    """The schema of one connector's database in one environment, as imported."""

    application: Name
    connector: Name
    environment: Name
    imported_at: datetime
    tables: list[TableSchema]
    truncated: str | None = Field(
        default=None,
        description="Set when tables were left out: imported 1000 of 1,240 tables.",
    )
