# Author: @SaadZarook
"""The plugin API: the only part of ProbeOwl a plugin may import.

A plugin gets what its manifest declares, through the context it is given, and nothing else. This
is an agreement checked by the context, an import test and review, not isolation: a plugin runs in
the same process as ProbeOwl. See docs/decisions.md.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, JsonValue, field_validator

from probeowl.comparison import (
    MASKED,
    ArrayMatch,
    Change,
    Comparison,
    Difference,
    Leaf,
    Options,
    Side,
    compare,
    compare_lines,
)
from probeowl.errors import CapabilityNotDeclared, InputTooLarge, RequestError
from probeowl.model.common import Name, ProbeOwlModel

# The comparison engine's entry points and their option and result types, nothing internal.
__all__ = [
    "MASKED",
    "ArrayMatch",
    "Capability",
    "CapabilityNotDeclared",
    "Change",
    "Comparison",
    "Difference",
    "InputTooLarge",
    "Leaf",
    "LocalInput",
    "Options",
    "PluginContext",
    "PluginManifest",
    "RequestError",
    "Side",
    "compare",
    "compare_lines",
]

# In docs/design.md as the target, and not built: nothing could grant them.
_NOT_BUILT = {"read_runs", "run_probes", "write_config", "outbound_http"}


class Capability(StrEnum):
    """What a plugin may be given. Only what is built can be declared."""

    LOCAL_INPUT = "local_input"


class PluginManifest(ProbeOwlModel):
    """A plugin's plugin.yaml: who it is, and what it needs."""

    name: Name
    version: str = Field(min_length=1)
    kind: Literal["tool", "integration"]
    author: str = Field(min_length=1)
    description: str = Field(min_length=1, description="One line.")
    capabilities: list[Capability] = Field(default_factory=list)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _only_what_is_built(cls, value: Any) -> Any:
        for name in value if isinstance(value, list) else []:
            if name in _NOT_BUILT:
                raise ValueError(
                    f"capability {name} is planned but not built; a plugin can declare only: "
                    f"{', '.join(c.value for c in Capability)}"
                )
        return value


@dataclass(frozen=True)
class LocalInput:
    """A file or text the person supplied in this session. Held in memory, never stored."""

    name: str
    text: str
    size: int
    sha256: str


Record = Callable[[str, dict[str, JsonValue]], None]


class PluginContext:
    """What ProbeOwl gives one plugin."""

    def __init__(self, manifest: PluginManifest, record: Record):
        self._manifest = manifest
        self._record = record

    @property
    def manifest(self) -> PluginManifest:
        """The plugin's own manifest."""
        return self._manifest

    def audit(self, what: str, details: dict[str, JsonValue] | None = None) -> None:
        """Record an action in the audit, with the actor and this plugin's name."""
        # Its own details last would let a plugin record another plugin's name.
        named: dict[str, JsonValue] = {"plugin": self._manifest.name, "what": what}
        self._record("plugin_action", (details or {}) | named)

    def local_input(self, name: str, text: str, max_bytes: int) -> LocalInput:
        """A file or text the person supplied, measured and hashed; InputTooLarge past max_bytes."""
        if Capability.LOCAL_INPUT not in self._manifest.capabilities:
            raise CapabilityNotDeclared(
                f"plugin {self._manifest.name} does not declare {Capability.LOCAL_INPUT.value}"
            )
        data = text.encode("utf-8")
        if len(data) > max_bytes:
            raise InputTooLarge(
                f"{name} is {len(data):,} bytes; the limit is {max_bytes:,} bytes per file"
            )
        return LocalInput(name, text, len(data), hashlib.sha256(data).hexdigest())
