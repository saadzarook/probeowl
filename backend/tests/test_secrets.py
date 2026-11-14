# Author: @SaadZarook
import re
import sys
import tomllib
from pathlib import Path
from typing import ClassVar

import keyring
import pytest
from jaraco.classes import properties
from keyring.backend import KeyringBackend
from keyring.backends.chainer import ChainerBackend
from keyring.errors import KeyringLocked, PasswordSetError

from probeowl import secrets
from probeowl.errors import ConfigError, KeychainUnavailable
from probeowl.secrets import check_keychain, resolve_secret, save_secret

BACKEND = Path(__file__).resolve().parents[1]
ENTRIES: dict[tuple[str, str], str] = {}  # what SystemKeychain holds, cleared for each test
REAL_KEYCHAINS = dict(secrets.KEYCHAINS)  # before any test points it at a fake


class SystemKeychain(KeyringBackend):
    """Stands in for the operating system's keychain, in memory."""

    priority: float = 10
    asked: ClassVar[list[tuple[str, str]]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.asked.append((service, username))
        return ENTRIES.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        ENTRIES[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del ENTRIES[(service, username)]


class FailingKeychain(SystemKeychain):
    """A system keychain whose writes fail, as a locked or full one does."""

    def set_password(self, service: str, username: str, password: str) -> None:
        raise PasswordSetError("the keychain refused the write")


class NoKeychain(SystemKeychain):
    """A system keychain that is not available here, as on a server with no desktop session."""

    @properties.classproperty
    def priority(cls) -> float:
        # As keyring's own backends say they can't run: viable is then False.
        raise RuntimeError("no keychain service here")


class FileKeyring(KeyringBackend):
    """Stands in for a file or registry backend: accepts anything, and must never be used."""

    priority: float = 20  # above the keychain, as keyring would rank a backend it was told to use
    entries: ClassVar[dict[tuple[str, str], str]] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.entries.pop((service, username), None)


def keychain_class(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """This machine's keychain is the class named here, in this module."""
    monkeypatch.setitem(secrets.KEYCHAINS, sys.platform, ((__name__, name),))


@pytest.fixture(autouse=True)
def memory_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real functions, over an in-memory keychain; a file keyring is everywhere else."""
    ENTRIES.clear()
    SystemKeychain.asked = []
    FileKeyring.entries = {}
    keychain_class(monkeypatch, "SystemKeychain")
    # keyring's own default, which ProbeOwl must never ask: a file keyring, as a stray
    # PYTHON_KEYRING_BACKEND or the dev dependencies would make it.
    monkeypatch.setattr(keyring, "get_keyring", lambda: FileKeyring())


def use(monkeypatch: pytest.MonkeyPatch, entries: dict[tuple[str, str], str]) -> None:
    ENTRIES.update(entries)


def test_env_reference_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROBEOWL_TEST_PW", "from-env")
    assert resolve_secret("env:PROBEOWL_TEST_PW") == "from-env"


def test_missing_env_variable_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROBEOWL_TEST_PW", raising=False)
    with pytest.raises(ConfigError, match="PROBEOWL_TEST_PW is not set"):
        resolve_secret("env:PROBEOWL_TEST_PW")


def test_keyring_reference_resolves_from_the_system_keychain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(monkeypatch, {("probeowl", "wallet-prod"): "from-keychain"})
    assert resolve_secret("keyring:probeowl/wallet-prod") == "from-keychain"
    assert SystemKeychain.asked == [("probeowl", "wallet-prod")]


def test_missing_keyring_entry_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="keyring set probeowl wallet-prod"):
        resolve_secret("keyring:probeowl/wallet-prod")


def test_unreadable_keyring_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(self: SystemKeychain, service: str, username: str) -> str:
        raise KeyringLocked("locked")

    monkeypatch.setattr(SystemKeychain, "get_password", locked)
    with pytest.raises(ConfigError, match="keychain could not be read"):
        resolve_secret("keyring:probeowl/wallet-prod")


# --- Only the system keychain -----------------------------------------------------------------


def test_the_keychains_are_exactly_the_operating_systems() -> None:
    assert REAL_KEYCHAINS == {
        "win32": (("keyring.backends.Windows", "WinVaultKeyring"),),
        "darwin": (("keyring.backends.macOS", "Keyring"),),
        "linux": (
            ("keyring.backends.SecretService", "Keyring"),
            ("keyring.backends.kwallet", "DBusKeyring"),
            ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
        ),
    }


def test_a_failed_write_is_refused_and_nothing_is_written_anywhere_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # keyring's default here is a chain whose first backend, the keychain, fails: the chain
    # would fall back to the file behind it. ProbeOwl never asks the chain.
    file = FileKeyring()
    monkeypatch.setattr(ChainerBackend, "backends", [FailingKeychain(), file])
    monkeypatch.setattr(keyring, "get_keyring", lambda: ChainerBackend())
    keychain_class(monkeypatch, "FailingKeychain")
    with pytest.raises(KeychainUnavailable, match="could not be written") as failure:
        save_secret("probeowl", "wallet_db.prod", "s3cret")
    assert "s3cret" not in str(failure.value)
    assert file.entries == {}
    assert ENTRIES == {}


def test_with_only_file_backends_saving_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    keychain_class(monkeypatch, "NoKeychain")
    with pytest.raises(KeychainUnavailable, match="no system keychain on this machine"):
        check_keychain()
    with pytest.raises(KeychainUnavailable, match="no system keychain on this machine"):
        save_secret("probeowl", "wallet_db.prod", "s3cret")
    assert FileKeyring.entries == {}


def test_with_no_keychain_a_secret_in_a_file_is_never_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FileKeyring.entries = {("probeowl", "wallet-prod"): "from-file"}
    keychain_class(monkeypatch, "NoKeychain")
    with pytest.raises(KeychainUnavailable, match="no system keychain") as failure:
        resolve_secret("keyring:probeowl/wallet-prod")
    assert "from-file" not in str(failure.value)
    with pytest.raises(KeychainUnavailable):
        secrets.read_keychain("probeowl", "wallet-prod")


def test_a_platform_with_no_known_keychain_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets.sys, "platform", "freebsd14")
    with pytest.raises(KeychainUnavailable, match="no system keychain"):
        check_keychain()


def test_python_keyring_backend_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", f"{__name__}.FileKeyring")
    save_secret("probeowl", "wallet_db.prod", "s3cret")
    assert ENTRIES == {("probeowl", "wallet_db.prod"): "s3cret"}
    assert FileKeyring.entries == {}


def test_deleting_an_entry_removes_it_from_the_keychain_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(monkeypatch, {("probeowl", "wallet_db.prod"): "s3cret"})
    FileKeyring.entries = {("probeowl", "wallet_db.prod"): "in-file"}
    secrets.delete_keychain("probeowl", "wallet_db.prod")
    secrets.delete_keychain("probeowl", "not-there")  # nothing to remove: no error
    assert ENTRIES == {}
    assert FileKeyring.entries == {("probeowl", "wallet_db.prod"): "in-file"}


PACKAGE = BACKEND / "probeowl"
# Anything that would let packaged code reach a keyring other than KEYCHAINS: the old CI switch,
# keyrings.alt, or keyring's default backend (a chain, or what PYTHON_KEYRING_BACKEND names).
FORBIDDEN = re.compile(
    r"PROBEOWL_ALLOW_INSECURE_KEYRING"
    r"|^\s*(?:import|from)\s+keyrings\b"
    r"|\bget_keyring\s*\("
    r"|\bkeyring\.(?:get|set|delete)_password\b"
    r"|\bkeyring\.(?:get_credential|set_keyring|core)\b",
    re.M,
)


def test_no_packaged_code_can_reach_a_file_keyring() -> None:
    found = [
        f"{path.relative_to(BACKEND).as_posix()}: {match.group(0).strip()}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for match in FORBIDDEN.finditer(path.read_text(encoding="utf-8"))
    ]
    assert found == []


@pytest.mark.parametrize(
    "line",
    [
        'os.environ.get("PROBEOWL_ALLOW_INSECURE_KEYRING")',
        "from keyrings.alt.file import PlaintextKeyring",
        "import keyrings.alt",
        "backend = keyring.get_keyring()",
        "keyring.set_password(service, username, value)",
    ],
)
def test_the_scan_finds_what_it_forbids(line: str) -> None:
    assert FORBIDDEN.search(f"x = 1\n    {line}\n")


def test_keyrings_alt_is_not_a_dependency_at_all() -> None:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    every = project["project"]["dependencies"] + project["dependency-groups"]["dev"]
    assert not [d for d in every if d.lower().startswith(("keyrings-alt", "keyrings.alt"))]


# --- Other references -------------------------------------------------------------------------


def test_vault_is_not_supported_yet() -> None:
    with pytest.raises(ConfigError, match="vault references are not supported yet"):
        resolve_secret("vault://prod/wallet-ro")


@pytest.mark.parametrize("ref", ["hunter2", "keyring:probeowl", "keyring:/user"])
def test_malformed_reference_is_a_config_error(ref: str) -> None:
    with pytest.raises(ConfigError):
        resolve_secret(ref)


def test_error_never_contains_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROBEOWL_TEST_PW", "s3cret-value")
    with pytest.raises(ConfigError) as err:
        resolve_secret("vault://prod/wallet-ro")
    assert "s3cret-value" not in str(err.value)


def test_owl_init_saves_a_password_in_the_system_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    save_secret("probeowl", "wallet_db.prod", "s3cret")
    assert ENTRIES == {("probeowl", "wallet_db.prod"): "s3cret"}
    assert resolve_secret("keyring:probeowl/wallet_db.prod") == "s3cret"
