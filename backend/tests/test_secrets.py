# Author: @SaadZarook
import tomllib
from pathlib import Path
from typing import Any

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends.chainer import ChainerBackend
from keyring.errors import KeyringLocked
from keyrings.alt.file import PlaintextKeyring

from probeowl.errors import ConfigError
from probeowl.secrets import ALLOW_INSECURE_KEYRING, check_keychain, resolve_secret, save_secret

BACKEND = Path(__file__).resolve().parents[1]


class SystemKeychain(KeyringBackend):
    """Stands in for the operating system's keychain."""

    priority = 10

    def __init__(self, entries: dict[tuple[str, str], str] | None = None):
        super().__init__()
        self.entries = entries or {}
        self.asked: list[tuple[str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.asked.append((service, username))
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        raise NotImplementedError


def plaintext(entries: dict[tuple[str, str], str]) -> PlaintextKeyring:
    """A real keyrings.alt backend, answering from memory instead of its file."""
    backend = PlaintextKeyring()
    backend.get_password = lambda service, username: entries.get((service, username))
    return backend


def use(monkeypatch: pytest.MonkeyPatch, backend: Any) -> None:
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)


@pytest.fixture(autouse=True)
def no_insecure_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_INSECURE_KEYRING, raising=False)


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
    keychain = SystemKeychain({("probeowl", "wallet-prod"): "from-keychain"})
    use(monkeypatch, keychain)
    assert resolve_secret("keyring:probeowl/wallet-prod") == "from-keychain"
    assert keychain.asked == [("probeowl", "wallet-prod")]


def test_missing_keyring_entry_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, SystemKeychain())
    with pytest.raises(ConfigError, match="keyring set probeowl wallet-prod"):
        resolve_secret("keyring:probeowl/wallet-prod")


def test_unreadable_keyring_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    keychain = SystemKeychain()

    def locked(service: str, username: str) -> str:
        raise KeyringLocked("locked")

    keychain.get_password = locked  # type: ignore[method-assign]
    use(monkeypatch, keychain)
    with pytest.raises(ConfigError, match="keychain could not be read"):
        resolve_secret("keyring:probeowl/wallet-prod")


# --- Insecure keyring backends ----------------------------------------------------------------


def test_a_plaintext_keyring_is_refused_with_the_setting_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use(monkeypatch, plaintext({("probeowl", "wallet-prod"): "s3cret-from-file"}))
    with pytest.raises(ConfigError) as failure:
        resolve_secret("keyring:probeowl/wallet-prod")
    message = str(failure.value)
    assert "keyrings.alt.file.PlaintextKeyring" in message
    assert "keeps secrets in files" in message
    assert f"{ALLOW_INSECURE_KEYRING}=1" in message
    assert "s3cret-from-file" not in message


def test_a_plaintext_keyring_is_read_when_ci_allows_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOW_INSECURE_KEYRING, "1")
    use(monkeypatch, plaintext({("probeowl", "wallet-prod"): "from-file"}))
    assert resolve_secret("keyring:probeowl/wallet-prod") == "from-file"


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_only_exactly_1_allows_an_insecure_keyring(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ALLOW_INSECURE_KEYRING, value)
    use(monkeypatch, plaintext({("probeowl", "wallet-prod"): "from-file"}))
    with pytest.raises(ConfigError, match=ALLOW_INSECURE_KEYRING):
        resolve_secret("keyring:probeowl/wallet-prod")


def test_in_a_chain_only_the_system_keychain_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # As on a developer machine with the dev dependencies: the system keychain first, then
    # keyrings.alt's file backends. A secret only in a file is not found, not read from it.
    keychain = SystemKeychain({("probeowl", "in-keychain"): "from-keychain"})
    chain = ChainerBackend()
    monkeypatch.setattr(
        ChainerBackend, "backends", [keychain, plaintext({("probeowl", "in-file"): "from-file"})]
    )
    use(monkeypatch, chain)
    assert resolve_secret("keyring:probeowl/in-keychain") == "from-keychain"
    with pytest.raises(ConfigError, match="no keychain entry"):
        resolve_secret("keyring:probeowl/in-file")
    monkeypatch.setenv(ALLOW_INSECURE_KEYRING, "1")
    assert resolve_secret("keyring:probeowl/in-file") == "from-file"


def test_keyrings_alt_is_a_dev_dependency_only() -> None:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = " ".join(project["project"]["dependencies"]).lower()
    dev = " ".join(project["dependency-groups"]["dev"]).lower()
    assert "keyrings" not in runtime
    assert "keyrings-alt" in dev or "keyrings.alt" in dev


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
    keychain = SystemKeychain()
    use(monkeypatch, keychain)
    save_secret("probeowl", "wallet_db.prod", "s3cret")
    assert keychain.entries == {("probeowl", "wallet_db.prod"): "s3cret"}
    assert resolve_secret("keyring:probeowl/wallet_db.prod") == "s3cret"


def test_a_password_is_never_saved_to_a_plaintext_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = plaintext({})
    written: list[str] = []
    backend.set_password = lambda service, username, password: written.append(password)
    use(monkeypatch, backend)
    with pytest.raises(ConfigError, match="keeps secrets in files"):
        check_keychain()
    with pytest.raises(ConfigError, match="keeps secrets in files"):
        save_secret("probeowl", "wallet_db.prod", "s3cret")
    assert written == []
