# Author: @SaadZarook
"""Global search's results: what the palette lists, grouped by kind (#38)."""

from enum import StrEnum

from pydantic import Field

from probeowl.model.common import Name, ProbeOwlModel


class SearchKind(StrEnum):
    """What a result is. Groups come back in this order."""

    APPLICATION = "application"
    ENTITY = "entity"
    PROBE = "probe"
    LOCATION = "location"
    CONNECTOR = "connector"
    RUNBOOK = "runbook"
    TABLE = "table"
    COLUMN = "column"
    RUN = "run"


class SearchItem(ProbeOwlModel):
    """One match: enough to show it and to build its link."""

    kind: SearchKind
    application: Name | None = Field(description="Its application; null only for an application.")
    id: str = Field(
        description=(
            "entity: visitor; location: visitor.cards; table: <connector>.<table>; "
            "column: <connector>.<table>.<column>; run: its id; otherwise its name."
        )
    )
    title: str = Field(description="The human title, or the id when there is none.")
    detail: str | None = Field(
        default=None,
        description=(
            "entity: its root key; probe: Validate · visitor; connector: mariadb · uat, prod; "
            "run: Failed · 12 Nov 14:02 · visitor_cards."
        ),
    )
    labels: list[str] = Field(
        default_factory=list,
        description=(
            "table: its catalog label, or the suggested one and 'suggested'; column: its roles."
        ),
    )


class SearchGroup(ProbeOwlModel):
    """The matches of one kind, best first."""

    kind: SearchKind
    items: list[SearchItem]
    more: int = Field(ge=0, description="Matches beyond the limit.")


class SearchResults(ProbeOwlModel):
    """Every group with a match, in SearchKind's order; a kind with none is left out."""

    query: str
    groups: list[SearchGroup]
