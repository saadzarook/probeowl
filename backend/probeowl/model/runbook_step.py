# Author: @SaadZarook
"""A runbook's steps in one run: what filled in, what ran, and what was ticked."""

from datetime import datetime

from pydantic import Field

from probeowl.model.common import ProbeOwlModel
from probeowl.model.run import Evidence, Outcome


class StepResult(ProbeOwlModel):
    """What running a read query found. Values are masked, and it is linked to its run."""

    outcome: Outcome
    reason: str | None = Field(default=None, description="Why, when the lookup failed.")
    evidence: Evidence | None = None
    warnings: list[str] = Field(default_factory=list)
    actor: str
    at: datetime


class UnfilledParameter(ProbeOwlModel):
    """A parameter left as :name, and why. A masked value is never filled in."""

    parameter: str
    why: str


class StepInRun(ProbeOwlModel):
    """One step of a runbook, as it stands in one run."""

    id: str
    filled: str | None = Field(
        default=None, description="Its SQL or query with this run's values. Null if it has none."
    )
    unfilled: list[UnfilledParameter] = Field(default_factory=list)
    done: bool = Field(default=False, description="A manual step's tick, saved with the run.")
    result: StepResult | None = Field(default=None, description="The latest run of a read query.")


class StepRan(ProbeOwlModel):
    """A step that ran: a read query's result, or the run a probe step started."""

    id: str
    result: StepResult | None = None
    run: str | None = Field(default=None, description="The run a probe step started.")


class CopiedWriteQuery(ProbeOwlModel):
    """A write query to take to a change request. Copying it was recorded in the audit."""

    text: str = Field(description="Its SQL under a header naming the environment and the run.")
    unfilled: list[UnfilledParameter] = Field(default_factory=list)
