# Author: @SaadZarook
"""Resolve secret references to credentials at run time. Values are never logged."""

import os

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError

from probeowl.errors import ConfigError, KeychainUnavailable

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
        raise KeychainUnavailable(f"the system keychain could not be read: {err}") from err
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
        raise KeychainUnavailable(f"the system keychain could not be written: {err}") from err


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
        raise KeychainUnavailable(
            f"the keyring backend ({names}) keeps secrets in files, not in the operating "
            "system's keychain, so ProbeOwl will not read it. Use the system keychain; "
            f"only in CI, set {ALLOW_INSECURE_KEYRING}=1 to allow it"
        )
    return secure


def keychain_entry(ref: str) -> tuple[str, str] | None:
    """The (service, username) a keyring: reference names; None for any other reference."""
    if not ref.startswith("keyring:"):
        return None
    service, _, username = ref.removeprefix("keyring:").partition("/")
    if not service or not username:
        raise ConfigError(f"keyring reference needs <service>/<username>, got {ref!r}")
    return service, username


def secret_saved(ref: str) -> bool:
    """Whether this machine has the secret a keyring: reference names. Never returns the value.

    True for any other reference: an env: variable is read where it is needed, as before.
    """
    entry = keychain_entry(ref)
    return entry is None or read_keychain(*entry) is not None


def read_keychain(service: str, username: str) -> str | None:
    """The keychain's value for this entry, or None when there is none. Never logged."""
    try:
        for backend in _keychains():
            value = backend.get_password(service, username)
            if value is not None:
                return value
    except KeyringError as err:
        raise KeychainUnavailable(f"the system keychain could not be read: {err}") from err
    return None


def delete_keychain(service: str, username: str) -> None:
    """Remove the entry from the keychain it is saved in; nothing if there is none."""
    try:
        for backend in _keychains():
            if backend.get_password(service, username) is not None:
                backend.delete_password(service, username)
    except KeyringError as err:
        raise KeychainUnavailable(f"the system keychain could not be written: {err}") from err
