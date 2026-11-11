# Author: @SaadZarook
"""Application and environment models."""

from pydantic import Field

from probeowl.model.common import Name, ProbeOwlModel


class Environment(ProbeOwlModel):
    """Production, UAT, Staging or Dev within an application."""

    title: str = Field(min_length=1, description="Shown in the UI, such as Production or UAT.")
    production: bool = Field(
        default=False,
        description=(
            "Production rules apply: read replicas only, guards cannot be relaxed, "
            "reason and reference mandatory on every investigation."
        ),
    )


class Application(ProbeOwlModel):
    """A product area that owns its connectors, entities and probes."""

    application: Name
    title: str = Field(min_length=1)
    owner: str = Field(min_length=1, description="The team responsible for this application.")
    environments: dict[Name, Environment] = Field(min_length=1)
