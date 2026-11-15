# Author: @SaadZarook
"""Resolve secret references to credentials at run time. Values are never logged.

A keyring: secret lives only in the operating system's keychain. ProbeOwl picks that keychain
itself, from the classes named below, and never asks keyring for its default backend: that can be
a chain that falls back to a file or the registry when the keychain fails, or whatever
PYTHON_KEYRING_BACKEND or a keyringrc.cfg names. With none of them available, a save is refused
and a read finds nothing; no other backend is ever tried.
"""

import importlib
import os
import sys

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError

from probeowl.errors import ConfigError, KeychainUnavailable

# The operating system's keychains, by platform, in the order they are tried.
KEYCHAINS: dict[str, tuple[tuple[str, str], ...]] = {
    "win32": (("keyring.backends.Windows", "WinVaultKeyring"),),
    "darwin": (),
    "linux": (
        ("keyring.backends.SecretService", "Keyring"),
        ("keyring.backends.kwallet", "DBusKeyring"),
        ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
    ),
}
NO_KEYCHAIN = (
    "there is no system keychain on this machine: ProbeOwl keeps passwords only in Windows "
    "Credential Manager, the macOS Keychain, or Secret Service or KWallet on Linux"
)


def without_line_break(value: str) -> str:
    """value without line breaks at its end (CR, LF or both), as one pasted or exported from a
    file carries; nothing else is ever changed (#84)."""
    return value.rstrip("\r\n")


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
    value = read_keychain(service, username)
    if value is None:
        raise ConfigError(
            f"no keychain entry for service {service!r} and user {username!r}; "
            f"add it with: keyring set {service} {username}"
        )
    return value


def save_secret(service: str, username: str, value: str) -> None:
    """Save a credential in the system keychain; KeychainUnavailable if there is none.

    A failed write is refused, never retried in another backend.
    """
    keychain = _keychain()
    try:
        keychain.set_password(service, username, value)
    except KeyringError as err:
        raise KeychainUnavailable(f"the system keychain could not be written: {err}") from err


def check_keychain() -> None:
    """KeychainUnavailable, saying why, if no system keychain can hold a credential."""
    _keychain()


def _from_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"environment variable {name} is not set")
    return value


def _keychain() -> KeyringBackend:
    """This machine's system keychain, from KEYCHAINS only; KeychainUnavailable if none works."""
    for module, name in KEYCHAINS.get(sys.platform, ()):
        try:
            backend: type[KeyringBackend] = getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError):
            continue  # this keyring release has no such class
        if backend.viable:
            return backend()
    raise KeychainUnavailable(NO_KEYCHAIN)


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
    keychain = _keychain()
    try:
        return keychain.get_password(service, username)
    except KeyringError as err:
        raise KeychainUnavailable(f"the system keychain could not be read: {err}") from err


def delete_keychain(service: str, username: str) -> None:
    """Remove the entry from the keychain it is saved in; nothing if there is none."""
    keychain = _keychain()
    try:
        if keychain.get_password(service, username) is not None:
            keychain.delete_password(service, username)
    except KeyringError as err:
        raise KeychainUnavailable(f"the system keychain could not be written: {err}") from err
