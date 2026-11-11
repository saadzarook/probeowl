# Author: @SaadZarook
"""What a healthy comparison returns. Masked values never appear: only whether they match."""

from enum import StrEnum

from pydantic import Field, JsonValue

from probeowl.model.common import Name, ProbeOwlModel
from probeowl.model.run import HealthyAttempt


class FieldResult(StrEnum):
    """How a field compares. Expected to vary: it differs, and is not a likely cause."""

    SAME = "same"
    DIFFERENT = "different"
    EXPECTED_TO_VARY = "expected_to_vary"
    NOT_COMPARED = "not_compared"


class ComparedField(ProbeOwlModel):
    """One location's outcome and rows, or one compare field, on the two subjects."""

    location: Name
    connector: Name
    field: str | None = Field(
        default=None, description="The compare field. Null: the location's outcome and rows."
    )
    failing: JsonValue = Field(default=None, description="Always null for a masked field.")
    healthy: JsonValue = Field(default=None, description="Always null for a masked field.")
    result: FieldResult
    masked: bool = False


class HealthyChoice(ProbeOwlModel):
    """A closer match a person can choose."""

    name: Name
    title: str


class HealthyComparison(ProbeOwlModel):
    """A run compared with a known-good subject, or what it takes to start one."""

    run: str
    has_sample: bool = Field(description="False: the entity has no sample, so an ID is pasted.")
    choices: list[HealthyChoice] = Field(default_factory=list)
    attempt: HealthyAttempt | None = Field(
        default=None, description="The latest attempt. Null until one has been started."
    )
    failing_subject: str | None = None
    healthy_subject: str | None = None
    warning: str | None = Field(
        default=None, description="Set when the subject compared with did not pass."
    )
    differences: list[ComparedField] = Field(
        default_factory=list, description="Every location and compare field, in location order."
    )
