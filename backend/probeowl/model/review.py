# Author: @SaadZarook
"""The catalog under review: suggested labels, drift from the live schema, and probe usage."""

from pydantic import Field

from probeowl.model.catalog import ColumnRole, LinkTarget, TableLabel
from probeowl.model.common import Name, ProbeOwlModel


class LabelSuggestion(ProbeOwlModel):
    """A label and column roles guessed from a table's name and shape. A person confirms it."""

    label: TableLabel
    reasons: list[str] = Field(description="Why this label, such as 'created_at and status'.")
    columns: dict[str, list[ColumnRole]] = Field(
        default_factory=dict, description="Suggested roles for the columns that have one."
    )
    links: dict[str, LinkTarget] = Field(
        default_factory=dict,
        description="Where each column with a suggested link role points, as its links_to: "
        "a column named for another imported table's key, such as visitor_profile_id for "
        "visitor_profile.id.",
    )


class Drift(ProbeOwlModel):
    """One way the catalog or another environment differs from an environment's live schema."""

    environment: Name
    message: str


class TableReview(ProbeOwlModel):
    """One table: what the catalog says, what the imports show, and which probes read it."""

    connector: Name
    db_schema: str | None = Field(default=None, alias="schema")
    table: str
    label: TableLabel | None = Field(
        description="The confirmed label in catalog.yaml; null when the table is not in it."
    )
    suggestion: LabelSuggestion | None = Field(
        description="Guessed from the first environment it was imported in; null if never."
    )
    imported_in: list[Name] = Field(description="Environments with an import of this table.")
    drift: list[Drift] = Field(default_factory=list)
    used_by: list[Name] = Field(default_factory=list, description="Probes that read this table.")


class CatalogReview(ProbeOwlModel):
    """An application's tables to review: unconfirmed labels, drift, tables probes read."""

    application: Name
    tables: list[TableReview]
    unconfirmed: int = Field(description="Tables with a suggested label not yet in catalog.yaml.")
    drifted: int = Field(description="Tables that differ from a live schema somewhere.")
