# Author: @SaadZarook
"""The macOS Keychain, for real (#93): the keychain job on macos-latest runs these.

They write to this machine's keychain, so they run only on macOS with PROBEOWL_KEYCHAIN_TESTS=1,
under probeowl_test_* names only, removed before and after, as dev/release/init_answers.py does.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from secrets import token_hex

import keyring.backends.macOS
import pytest

from probeowl.errors import KeychainUnavailable
from probeowl.secrets import (
    _keychain,
    delete_keychain,
    read_keychain,
    resolve_secret,
    save_secret,
    secret_saved,
)

pytestmark = [
    pytest.mark.keychain,
    pytest.mark.skipif(
        sys.platform != "darwin" or os.environ.get("PROBEOWL_KEYCHAIN_TESTS") != "1",
        reason="writes to the real macOS Keychain: macOS with PROBEOWL_KEYCHAIN_TESTS=1 only",
    ),
]

SERVICE = "probeowl"
LOGIN = Path.home() / "Library" / "Keychains" / "login.keychain-db"


@pytest.fixture
def name() -> Iterator[str]:
    """A probeowl_test_* entry, absent before the test and removed after it."""
    found = f"probeowl_test_{token_hex(4)}.uat"
    delete_keychain(SERVICE, found)
    yield found
    delete_keychain(SERVICE, found)


def security(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["security", *args], capture_output=True, text=True, timeout=30)


def in_login_keychain(name: str) -> bool:
    """Whether the login keychain holds the entry; asked of that file, not the search list."""
    found = security("find-generic-password", "-s", SERVICE, "-a", name, str(LOGIN))
    assert found.returncode in (0, 44), found.stderr  # 44: the item could not be found
    return found.returncode == 0


def test_the_keychain_is_the_macos_keychain() -> None:
    assert type(_keychain()) is keyring.backends.macOS.Keyring


def test_a_password_round_trips_and_is_never_printed(
    name: str, capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    value = f"pw-{token_hex(12)}"
    save_secret(SERVICE, name, value)
    assert read_keychain(SERVICE, name) == value
    assert resolve_secret(f"keyring:{SERVICE}/{name}") == value
    assert secret_saved(f"keyring:{SERVICE}/{name}")
    delete_keychain(SERVICE, name)
    assert read_keychain(SERVICE, name) is None
    assert not secret_saved(f"keyring:{SERVICE}/{name}")
    out, err = capfd.readouterr()
    assert value not in out + err + caplog.text


def test_a_write_that_fails_is_refused_and_written_nowhere(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(*args: object) -> None:
        raise keyring.backends.macOS.api.Error(-25307, "no default keychain")

    # The Security call fails as it would with no default keychain; the rest is the real backend.
    monkeypatch.setattr(keyring.backends.macOS.api, "set_generic_password", refused)
    with pytest.raises(KeychainUnavailable, match="could not be written"):
        save_secret(SERVICE, name, "never-kept")
    monkeypatch.undo()
    assert read_keychain(SERVICE, name) is None
    assert not in_login_keychain(name)


# A write to a locked keychain, in its own process: if the runner shows an unlock prompt instead
# of refusing, the timeout fails the test rather than hanging the job.
LOCKED_WRITE = """
import sys
from probeowl.errors import KeychainUnavailable
from probeowl.secrets import save_secret
try:
    save_secret("probeowl", sys.argv[1], "never-kept")
except KeychainUnavailable as err:
    print("refused:", err)
    sys.exit(0)
sys.exit("written to a locked keychain")
"""


@pytest.fixture
def locked_default(tmp_path: Path) -> Iterator[Path]:
    """A throwaway keychain as the default, locked; the default before it is put back after."""
    before = security("default-keychain", "-d", "user").stdout.strip().strip('"')
    path = tmp_path / "probeowl-test.keychain-db"
    password = token_hex(24)  # this keychain's own, never printed or kept
    assert security("create-keychain", "-p", password, str(path)).returncode == 0
    try:
        assert security("default-keychain", "-d", "user", "-s", str(path)).returncode == 0
        assert security("lock-keychain", str(path)).returncode == 0
        yield path
    finally:
        security("default-keychain", "-d", "user", "-s", before)
        security("delete-keychain", str(path))


def test_a_locked_keychain_is_refused_and_written_nowhere(name: str, locked_default: Path) -> None:
    try:
        done = subprocess.run(
            [sys.executable, "-c", LOCKED_WRITE, name], capture_output=True, text=True, timeout=30
        )
    except subprocess.TimeoutExpired:
        pytest.fail("a write to a locked keychain waited (an unlock prompt?) instead of refusing")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "refused: the system keychain could not be written" in done.stdout
    assert "never-kept" not in done.stdout + done.stderr
    assert not in_login_keychain(name)
