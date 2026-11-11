# Author: @SaadZarook
"""Save checks: what a probe and its entity's lookups must pass before the probe is saved."""

from enum import StrEnum

from pydantic import Field

from probeowl.model.common import Name, ProbeOwlModel


class CheckStatus(StrEnum):
    """Pass; warn (saving is allowed, with the reason); or fail (saving is refused)."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class SaveCheck(ProbeOwlModel):
    """One check, such as Read-only SQL or the plan of one lookup."""

    check: str = Field(description="What was checked, such as 'Read-only SQL'.")
    status: CheckStatus
    message: str = Field(description="What was found, or what to change.")
    location: str | None = Field(
        default=None, description="The location or identifier it is about, if one."
    )
    environment: Name | None = Field(
        default=None, description="The environment a plan was read in, for plan checks."
    )
    plan_not_run: bool = Field(
        default=False,
        description="A plan check that could not run, so index use is unknown. It warns.",
    )


class SaveCheckReport(ProbeOwlModel):
    """Every save check of one draft probe. It can be saved when none failed."""

    probe: Name | None = Field(description="The draft's probe name, if it could be read.")
    entity: Name | None = None
    checks: list[SaveCheck]
    passed: int
    warnings: int
    failed: int
    can_save: bool
    plans_not_run: int = Field(
        default=0,
        description=(
            "Plan checks that could not run. In Production, saving then needs "
            "save_without_plan_check."
        ),
    )
