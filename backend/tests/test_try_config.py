# Author: @SaadZarook
"""dev/try_config.py: the examples copy dev/try.sh serves, and where its passwords go."""

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from probeowl.model.check import load_applications

ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "examples"
PASSWORD = "Try_Pw_Marker"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("try_config", ROOT / "dev" / "try_config.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try_config = load()


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(try_config, "save_secret", lambda s, u, v: found.__setitem__((s, u), v))
    return found


def connector(root: Path, app: str, name: str) -> dict[str, dict[str, dict[str, str]]]:
    path = root / app / "connectors" / f"{name}.yaml"
    loaded: dict[str, dict[str, dict[str, str]]] = yaml.safe_load(path.read_text("utf-8"))
    return loaded


def test_every_password_goes_under_probeowl_try_never_probeowl(
    saved: dict[tuple[str, str], str], tmp_path: Path
) -> None:
    out = tmp_path / "config"
    names = try_config.make_try_config(EXAMPLES, out, False, PASSWORD)
    assert {service for service, _ in saved} == {"probeowl-try"}
    assert set(saved.values()) == {PASSWORD}
    assert names == [f"probeowl-try/{user}" for _, user in sorted(saved)]
    for path in out.rglob("connectors/*.yaml"):
        text = path.read_text("utf-8")
        assert "keyring:probeowl/" not in text
        assert "env:" not in text
        assert PASSWORD not in text
    wallet_db = connector(out, "wallet", "wallet_db")["environments"]
    assert wallet_db["prod"]["secret"] == "keyring:probeowl-try/wallet_db.prod"
    assert wallet_db["uat"]["secret"] == "keyring:probeowl-try/wallet_db.uat"
    assert wallet_db["uat"]["database"] == "wallet_uat"
    # What owl serve will load.
    assert set(load_applications(out)) == {"wallet", "card_access"}


def test_native_gets_the_native_setups_test_names(
    saved: dict[tuple[str, str], str], tmp_path: Path
) -> None:
    out = tmp_path / "config"
    try_config.make_try_config(EXAMPLES, out, True, PASSWORD)
    ledger = connector(out, "wallet", "ledger_db")["environments"]
    assert ledger["uat"]["schema"] == "LEDGER_UAT_TEST"
    assert ledger["prod"]["user"] == "probeowl_test_ro"
    access = connector(out, "card-access", "access_db")["environments"]
    assert access["prod"]["database"] == "access_test"


def test_the_fake_apis_need_no_keychain_entry(
    saved: dict[tuple[str, str], str], tmp_path: Path
) -> None:
    out = tmp_path / "config"
    try_config.make_try_config(EXAMPLES, out, False, PASSWORD)
    checkout = connector(out, "wallet", "checkout_api")["environments"]
    assert "secret" not in checkout["prod"]
    assert not any(user.startswith("checkout_api") for _, user in saved)


def test_a_binding_off_this_machine_is_refused_and_nothing_is_saved(
    saved: dict[tuple[str, str], str], tmp_path: Path
) -> None:
    examples = tmp_path / "examples"

    shutil.copytree(EXAMPLES, examples)
    path = examples / "wallet" / "connectors" / "wallet_db.yaml"
    path.write_text(path.read_text("utf-8").replace("127.0.0.1", "db.example.com", 1), "utf-8")
    with pytest.raises(SystemExit, match=r"db.example.com is not on 127.0.0.1"):
        try_config.make_try_config(examples, tmp_path / "config", False, PASSWORD)
    assert saved == {}
    assert not (tmp_path / "config").exists()
