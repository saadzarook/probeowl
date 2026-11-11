# Author: @SaadZarook
"""Table catalog models: each table an application's probes read, its label and column roles."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from probeowl.model.common import Duration, FieldName, Name, ProbeOwlModel


class TableLabel(StrEnum):
    """What kind of table it is. Each label sets defaults for guards, masking and templates."""

    TRANSACTION = "transaction"
    MASTER = "master"
    AUDIT = "audit"
    LOG = "log"
    REFERENCE = "reference"
    EXTERNAL_MAPPING = "external_mapping"
    OUTBOX = "outbox"


class ColumnRole(StrEnum):
    """A marked column."""

    PRIMARY_KEY = "primary_key"
    TIME = "time"
    STATUS = "status"
    LINK = "link"
    EXTERNAL_KEY = "external_key"
    PII = "pii"
    COMPARE = "compare"
    EXPECTED_TO_VARY = "expected_to_vary"


# Large or growing tables must be read within a time window.
_NEEDS_TIME_COLUMN = {TableLabel.TRANSACTION, TableLabel.AUDIT, TableLabel.LOG}


class LinkTarget(ProbeOwlModel):
    """Where a link or external key column points: a table column, or another system's key."""

    connector: Name
    table: FieldName | None = None
    column: FieldName | None = None

    @model_validator(mode="after")
    def _column_needs_table(self) -> Self:
        if self.column is not None and self.table is None:
            raise ValueError("links_to.column needs links_to.table")
        return self


class Column(ProbeOwlModel):
    """One marked column and its roles."""

    roles: list[ColumnRole] = Field(min_length=1)
    indexed: bool | None = Field(default=None, description="For time columns: is it indexed?")
    values: list[str] = Field(
        default_factory=list, description="For status columns: the values it can hold."
    )
    links_to: LinkTarget | None = Field(
        default=None, description="For link and external key columns."
    )

    @model_validator(mode="after")
    def _role_fields(self) -> Self:
        if self.values and ColumnRole.STATUS not in self.roles:
            raise ValueError("values applies only to a status column")
        if self.indexed is not None and ColumnRole.TIME not in self.roles:
            raise ValueError("indexed applies only to a time column")
        if ColumnRole.EXPECTED_TO_VARY in self.roles and ColumnRole.COMPARE not in self.roles:
            raise ValueError("expected_to_vary applies only to a compare column")
        is_link = ColumnRole.LINK in self.roles or ColumnRole.EXTERNAL_KEY in self.roles
        if is_link != (self.links_to is not None):
            raise ValueError(
                "links_to is required for link and external key columns, and only them"
            )
        return self


class CatalogTable(ProbeOwlModel):
    """One table in the catalog, with its label, owner and marked columns."""

    connector: Name
    table: FieldName
    label: TableLabel
    owner: str = Field(min_length=1)
    description: str | None = None
    columns: dict[FieldName, Column] = Field(
        description="Marked columns only; unmarked columns are not listed."
    )
    retention: Duration | None = Field(default=None, description="How long rows are kept.")
    approx_rows: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _label_rules(self) -> Self:
        roles = [role for column in self.columns.values() for role in column.roles]
        if ColumnRole.PRIMARY_KEY not in roles:
            raise ValueError(f"{self.table}: mark the primary key column")
        time_columns = roles.count(ColumnRole.TIME)
        if time_columns > 1:
            raise ValueError(f"{self.table}: mark at most one time column")
        if self.label in _NEEDS_TIME_COLUMN and time_columns == 0:
            raise ValueError(f"{self.table}: a {self.label} table needs a time column")
        return self


class Catalog(ProbeOwlModel):
    """An application's table catalog."""

    catalog: Name = Field(description="The application this catalog belongs to.")
    tables: list[CatalogTable] = Field(min_length=1)

    @model_validator(mode="after")
    def _tables_unique(self) -> Self:
        seen = set()
        for table in self.tables:
            key = (table.connector, table.table.lower())
            if key in seen:
                raise ValueError(f"{table.connector}.{table.table} is listed twice")
            seen.add(key)
        return self
