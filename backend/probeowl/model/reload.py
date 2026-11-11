# Author: @SaadZarook
"""What a reload of the configuration loaded, and what it changed."""

from pydantic import Field

from probeowl.model.common import Name, ProbeOwlModel

RESTART_NEEDED = (
    "Plugins, the port, the store and the log file change only when owl serve is restarted."
)


class ConfigReload(ProbeOwlModel):
    """The configuration owl serve loaded again, and how it differs from the one before."""

    applications: int = Field(ge=0, description="Applications loaded.")
    connectors: int = Field(ge=0, description="Connectors loaded, across applications.")
    probes: int = Field(ge=0, description="Probes loaded, across applications.")
    applications_added: list[Name] = Field(default_factory=list)
    applications_removed: list[Name] = Field(default_factory=list)
    connectors_added: list[str] = Field(
        default_factory=list, description="Each as application/connector."
    )
    connectors_removed: list[str] = Field(
        default_factory=list, description="Each as application/connector."
    )
    connectors_changed: list[str] = Field(
        default_factory=list,
        description="Connectors whose file changed: a binding, a guard or their owner.",
    )
    note: str = Field(default=RESTART_NEEDED, description="What a reload leaves as it was.")
