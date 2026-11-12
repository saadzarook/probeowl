# Author: @SaadZarook
"""The single exception hierarchy for ProbeOwl."""

from collections.abc import Iterable


class ProbeOwlError(Exception):
    """Base class for every error ProbeOwl raises on purpose."""

    # RFC 9457: a URI naming the problem, for a client that acts on it, and its extra members.
    problem_type = "about:blank"

    def extensions(self) -> dict[str, str]:
        """Members a problem details response carries beside detail; none by default."""
        return {}


class ConfigError(ProbeOwlError):
    """A configuration file or value is invalid."""


class KeychainUnavailable(ConfigError):
    """The system keychain cannot be read or written on this machine."""


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


class SecretExists(ConflictError):
    """A password is saved under this keychain name already, and replacing it was not asked."""

    problem_type = "urn:probeowl:problem:secret-exists"

    def __init__(self, entry: str, connector: str, environment: str) -> None:
        super().__init__(
            f"a password is already saved as {entry}, perhaps for another application's "
            f"connector named {connector}; overwrite it only if it is this one's"
        )
        self.connector = connector
        self.environment = environment

    def extensions(self) -> dict[str, str]:
        """The binding, so a client can ask before it overwrites."""
        return {"connector": self.connector, "environment": self.environment}


class PasswordMissing(RequestError):
    """A binding a request needs has no password saved on this machine."""

    problem_type = "urn:probeowl:problem:password-missing"

    def __init__(self, connector: str, environment: str, title: str) -> None:
        super().__init__(
            f"{connector} needs your password for {title}: save it on this machine first",
            {"connector": f"no password saved on this machine for {environment}"},
        )
        self.connector = connector
        self.environment = environment

    def extensions(self) -> dict[str, str]:
        """The binding whose password to ask for."""
        return {"connector": self.connector, "environment": self.environment}


class CapacityError(ProbeOwlError):
    """ProbeOwl is already doing as much of this as it allows at once; try again shortly."""
