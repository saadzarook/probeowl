# Author: @SaadZarook
"""Runbook and step models. Write queries are stored for reference and never run."""

import hashlib
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from probeowl.model.common import Name, ProbeOwlModel

StepId = Annotated[
    str,
    Field(
        pattern=r"^[a-z][a-z0-9-]*$",
        max_length=64,
        description="Short and unique in its runbook. Results, ticks and the audit key on it.",
    ),
]
Date = date  # Review has a field named date, which would hide the type in its class body


def sql_sha256(sql: str) -> str:
    """The hash a review is tied to. Line endings and outer whitespace do not count as a change."""
    return hashlib.sha256(sql.replace("\r\n", "\n").strip().encode()).hexdigest()


class Review(ProbeOwlModel):
    """Who reviewed a read query, when, and exactly what they read."""

    by: str = Field(min_length=1)
    date: Date = Field(description="The 90-day review flag counts from this date.")
    sql_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="The reviewed SQL's hash. If the SQL changes, the step goes back to copy only.",
    )


class ReadQueryStep(ProbeOwlModel):
    """A single SELECT. Runnable only after review and save checks; until then copy only."""

    kind: Literal["read_query"]
    id: StepId
    title: str = Field(min_length=1)
    using: Name
    sql: str = Field(min_length=1)
    reviewed: Review | None = Field(
        default=None, description="Set in the reviewed PR. Without it the step is copy only."
    )


class WriteQueryStep(ProbeOwlModel):
    """A query that changes data. ProbeOwl never runs it; copying it is recorded in the audit."""

    kind: Literal["write_query"]
    id: StepId
    title: str = Field(min_length=1)
    using: Name
    sql: str = Field(min_length=1)
    requires: str = Field(min_length=1, description="The approval needed, such as change approval.")


class ExternalQueryStep(ProbeOwlModel):
    """A query for another tool, such as Dynatrace DQL. Copied, never run by ProbeOwl."""

    kind: Literal["external_query"]
    id: StepId
    title: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    query: str = Field(min_length=1)


class ManualStep(ProbeOwlModel):
    """A checklist item for a person."""

    kind: Literal["manual"]
    id: StepId
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ProbeStep(ProbeOwlModel):
    """Run a probe again, for example after a fix."""

    kind: Literal["probe"]
    id: StepId
    title: str = Field(min_length=1)
    probe: Name


# Named apart from the probe's Step.
RunbookStep = Annotated[
    ReadQueryStep | WriteQueryStep | ExternalQueryStep | ManualStep | ProbeStep,
    Field(discriminator="kind"),
]

LocationOutcome = Annotated[
    str,
    Field(
        pattern=(
            r"^[a-z][a-z0-9_]*\."
            r"(found|missing|waiting|blocked|ambiguous|error|stale|not_configured)$"
        ),
        description="<location>.<outcome>, such as ledger.missing.",
    ),
]


class AppliesTo(ProbeOwlModel):
    """When a failed run suggests this runbook."""

    entity: Name
    when: list[LocationOutcome] = Field(min_length=1)


class RunbookBase(ProbeOwlModel):
    """Runbook fields other than its steps, shared by the YAML file and the API response."""

    runbook: Name
    title: str = Field(min_length=1)
    application: Name
    applies_to: AppliesTo
    owner: str = Field(min_length=1)
    last_verified: date


class Runbook(RunbookBase):
    """An ordered list of steps for a symptom, stored in the knowledge base."""

    steps: list[RunbookStep] = Field(min_length=1)

    @model_validator(mode="after")
    def _step_ids_are_unique(self) -> Self:
        ids = [step.id for step in self.steps]
        repeated = sorted({step_id for step_id in ids if ids.count(step_id) > 1})
        if repeated:
            raise ValueError(f"step ids must be unique; repeated: {', '.join(repeated)}")
        return self
