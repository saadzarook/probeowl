# Author: @SaadZarook
"""The single exception hierarchy for ProbeOwl."""

from collections.abc import Iterable


class ProbeOwlError(Exception):
    """Base class for every error ProbeOwl raises on purpose."""


class ConfigError(ProbeOwlError):
    """A configuration file or value is invalid."""


class ConnectorError(ProbeOwlError):
    """A connector could not complete a lookup."""


class MissingGrant(ConnectorError):
    """The binding's login lacks a grant the database named, such as SELECT on SYS.V_$SESSION."""

    def __init__(self, grant: str) -> None:
        super().__init__(f"missing {grant}: a DBA grants it to the binding's login")
        self.grant = grant


class GuardViolation(ProbeOwlError):
    """A lookup or query was refused by a guard."""


class ReadsStopped(ProbeOwlError):
    """Nothing may read through this connector: the whole run stops, not one location."""


class KillSwitchEngaged(ReadsStopped):
    """A kill switch covers this connector."""


class KillSwitchUnknown(ReadsStopped):
    """The store cannot be read, so no kill switch can be shown to be off."""


class CapabilityNotDeclared(ProbeOwlError):
    """A plugin asked for something its manifest does not declare."""


class InputTooLarge(ProbeOwlError):
    """A file or text a person supplied is past the limit."""


class NotFoundError(ProbeOwlError):
    """A requested application, entity, probe or run does not exist."""


def unknown_application(name: str, known: Iterable[str]) -> NotFoundError:
    """The error for an application owl serve did not load, naming the ones it did."""
    loaded = sorted(known)
    if not loaded:
        return NotFoundError(
            f"no application named {name}: owl serve loaded none; run owl init, "
            "or restart owl serve --config <folder>"
        )
    return NotFoundError(f"no application named {name}; there are: {', '.join(loaded)}")


class RequestError(ProbeOwlError):
    """A request names things that exist but asks for something not allowed, field by field."""

    def __init__(self, message: str, fields: dict[str, str] | None = None):
        super().__init__(message)
        self.fields = fields or {}


class ConflictError(ProbeOwlError):
    """What a request was based on has changed since; look again and retry."""


class CapacityError(ProbeOwlError):
    """ProbeOwl is already doing as much of this as it allows at once; try again shortly."""
