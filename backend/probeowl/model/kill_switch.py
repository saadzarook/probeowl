# Author: @SaadZarook
"""The kill switch: stops every read, everywhere, for one application, or for one connector."""

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from probeowl.model.common import Name, ProbeOwlModel
from probeowl.model.run import Actor


class KillScope(StrEnum):
    """How much one kill switch stops."""

    GLOBAL = "global"
    APPLICATION = "application"
    CONNECTOR = "connector"  # in every environment of its application


class KillTarget(ProbeOwlModel):
    """What one kill switch covers."""

    scope: KillScope
    application: Name | None = Field(default=None, description="For application and connector.")
    connector: Name | None = Field(default=None, description="For connector.")

    @model_validator(mode="after")
    def _names_match_scope(self) -> Self:
        wanted = (
            self.scope is not KillScope.GLOBAL,
            self.scope is KillScope.CONNECTOR,
        )
        if (self.application is not None, self.connector is not None) != wanted:
            raise ValueError(
                "global names nothing; application names an application; "
                "connector names an application and a connector"
            )
        return self

    @property
    def key(self) -> str:
        """The target as one string, stored as the switch's identity."""
        return target_key(self.application, self.connector)


def target_key(application: str | None, connector: str | None) -> str:
    """The stored identity of a target: broader targets have fewer parts."""
    return "/".join(part for part in ("global", application, connector) if part)


class KillSwitch(KillTarget):
    """One kill switch that is on, and who turned it on and why."""

    reason: str = Field(min_length=1)
    reference: str | None = None
    actor: Actor
    engaged_at: datetime
