# Author: @SaadZarook
"""Resolve secret references to credentials at run time. Values are never logged."""

import os

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError

from probeowl.errors import ConfigError

ALLOW_INSECURE_KEYRING = "PROBEOWL_ALLOW_INSECURE_KEYRING"
# keyrings.alt backends keep secrets in files (plaintext or locally encrypted), not in the
# operating system's keychain.
_INSECURE_MODULES = ("keyrings.alt",)


def resolve_secret(ref: str) -> str:
    """Return the credential a keyring: or env: reference points at."""
    if ref.startswith("keyring:"):
        return _from_keyring(ref.removeprefix("keyring:"))
    if ref.startswith("env:"):
        return _from_env(ref.removeprefix("env:"))
    if ref.startswith("vault://"):
        raise ConfigError("vault references are not supported yet")
    raise ConfigError(
        f"unknown secret reference {ref!r}: use keyring:<service>/<username> or env:<VARIABLE>"
    )


def _from_keyring(target: str) -> str:
    service, _, username = target.partition("/")
    if not service or not username:
        raise ConfigError(f"keyring reference needs <service>/<username>, got {target!r}")
    value = None
    try:
        for backend in _keychains():
            value = backend.get_password(service, username)
            if value is not None:
                break
    except KeyringError as err:
        raise ConfigError(f"the system keychain could not be read: {err}") from err
    if value is None:
        raise ConfigError(
            f"no keychain entry for service {service!r} and user {username!r}; "
            f"add it with: keyring set {service} {username}"
        )
    return value


def save_secret(service: str, username: str, value: str) -> None:
    """Save a credential in the system keychain; ConfigError if only a file-based one exists."""
    try:
        _keychains()[0].set_password(service, username, value)
    except KeyringError as err:
        raise ConfigError(f"the system keychain could not be written: {err}") from err


def check_keychain() -> None:
    """ConfigError, saying why, if no system keychain can hold a credential."""
    _keychains()


def _from_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"environment variable {name} is not set")
    return value


def _keychains() -> list[KeyringBackend]:
    """The keyring backends ProbeOwl may read: secure ones only, unless CI allows the rest."""
    active = keyring.get_keyring()
    # The default backend chains several; each one is judged on its own.
    backends: list[KeyringBackend] = list(getattr(active, "backends", [active]))
    if os.environ.get(ALLOW_INSECURE_KEYRING) == "1":
        return backends
    secure = [b for b in backends if not type(b).__module__.startswith(_INSECURE_MODULES)]
    if not secure:
        names = ", ".join(f"{type(b).__module__}.{type(b).__name__}" for b in backends)
        raise ConfigError(
            f"the keyring backend ({names}) keeps secrets in files, not in the operating "
            "system's keychain, so ProbeOwl will not read it. Use the system keychain; "
            f"only in CI, set {ALLOW_INSECURE_KEYRING}=1 to allow it"
        )
    return secure
