# Author: @SaadZarook
"""Confirming a table's label into catalog.yaml through the API, under the write rules."""

import shutil
from pathlib import Path
from typing import Any

import pytest

import probeowl.catalog.service
from probeowl.api.app import create_app
from probeowl.catalog.service import CatalogService
from probeowl.config_folder import ConfigFolder
from probeowl.model.check import check_folder, load_applications
from probeowl.store.audit import list_events
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.audit_fails import audit_fails
from tests.local_client import local_client
from tests.test_config_folder import link

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
WALLETS = {
    "connector": "wallet_db",
    "table": "wallets",
    "label": "master",
    "owner": "payments-team",
    "description": "One wallet per user.",
    "columns": {
        "id": {"roles": ["primary_key"]},
        "user_id": {"roles": ["pii"]},
    },
}
URL = "/api/applications/wallet/catalog/tables/wallet_db/wallets"


class Folder:
    """A copy of the examples served by owl serve's catalog, and its store."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "config"
        shutil.copytree(EXAMPLES, self.root)
        self.store = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
        upgrade(self.store)
        self.applications = load_applications(self.root)
        catalog = CatalogService(
            self.applications, self.store, actor_name="s.khan", folder=ConfigFolder(self.root)
        )
        self.api = local_client(create_app(None, catalog))

    def catalog(self, application: str = "wallet") -> str:
        return (self.root / application / "catalog.yaml").read_text(encoding="utf-8")

    def events(self) -> list[Any]:
        with self.store.connect() as conn:
            return [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action.startswith("catalog")]


@pytest.fixture
def folder(tmp_path: Path) -> Folder:
    return Folder(tmp_path)


def confirm(folder: Folder, base: str | None = None, entry: dict[str, Any] = WALLETS) -> Any:
    body: dict[str, Any] = {"entry": entry} | ({"base": base} if base is not None else {})
    return folder.api.put(URL, json=body)


def test_without_base_the_change_is_shown_and_nothing_is_written(folder: Folder) -> None:
    before = folder.catalog()
    response = confirm(folder)
    assert response.status_code == 200, response.text
    change = response.json()
    assert change["written"] is False
    assert change["path"] == "wallet/catalog.yaml"
    assert "+  - connector: wallet_db\n+    table: wallets\n+    label: master\n" in change["diff"]
    # Only the confirmed table: every other line of the file is untouched.
    assert all(
        line.startswith(("+", "---", "+++", "@@", " ")) for line in change["diff"].splitlines()
    )
    assert folder.catalog() == before
    assert folder.events() == []


def test_with_the_preview_base_exactly_that_change_is_written_and_audited(folder: Folder) -> None:
    preview = confirm(folder).json()
    response = confirm(folder, preview["base"])
    assert response.status_code == 200, response.text
    written = response.json()
    assert written["written"] is True and written["diff"] == preview["diff"]
    assert "table: wallets" in folder.catalog()
    assert check_folder(folder.root).findings == []  # owl check still passes
    [event] = folder.events()
    assert (event.action, event.actor.name, event.application) == (
        "catalog_table_confirmed",
        "s.khan",
        "wallet",
    )
    assert event.details == {
        "path": "wallet/catalog.yaml",
        "connector": "wallet_db",
        "table": "wallets",
        "label": "master",
        "base": preview["base"],
        "after": written["after"],
    }
    # Served at once, without restarting owl serve.
    catalog = folder.api.get("/api/applications/wallet/catalog").json()
    assert "wallets" in {t["table"] for t in catalog["tables"]}


def test_an_existing_entry_is_replaced_in_place(folder: Folder) -> None:
    webhooks = {
        "connector": "wallet_db",
        "table": "provider_webhooks",
        "label": "log",
        "owner": "payments-team",
        "retention": "14d",
        "columns": {
            "id": {"roles": ["primary_key"]},
            "received_at": {"roles": ["time"], "indexed": True},
        },
    }
    url = "/api/applications/wallet/catalog/tables/wallet_db/provider_webhooks"
    preview = folder.api.put(url, json={"entry": webhooks}).json()
    assert "-    retention: 30d\n+    retention: 14d\n" in preview["diff"]
    folder.api.put(url, json={"entry": webhooks, "base": preview["base"]})
    text = folder.catalog()
    assert text.count("table: provider_webhooks") == 1
    # The comment elsewhere in the file is kept.
    assert "# A transaction has two rows, so (txn_id, direction) identifies one." in text


def test_a_file_changed_since_the_preview_is_a_409_and_not_written(folder: Folder) -> None:
    preview = confirm(folder).json()
    path = folder.root / "wallet" / "catalog.yaml"
    edited = folder.catalog() + "# edited by hand\n"
    path.write_text(edited, encoding="utf-8")
    response = confirm(folder, preview["base"])
    assert response.status_code == 409
    assert "changed since its preview" in response.json()["detail"]
    assert folder.catalog() == edited
    assert folder.events() == []


def test_an_application_without_a_catalog_gets_one_in_its_folder(folder: Folder) -> None:
    (folder.root / "card-access" / "catalog.yaml").unlink()
    folder.applications.update(load_applications(folder.root))
    entry = {
        "connector": "access_db",
        "table": "cards",
        "label": "master",
        "owner": "access-team",
        "columns": {"card_id": {"roles": ["primary_key"]}},
    }
    url = "/api/applications/card_access/catalog/tables/access_db/cards"
    preview = folder.api.put(url, json={"entry": entry}).json()
    assert preview["base"] == "absent"
    assert preview["path"] == "card-access/catalog.yaml"
    folder.api.put(url, json={"entry": entry, "base": preview["base"]})
    assert check_folder(folder.root).findings == []
    assert folder.catalog("card-access").startswith("# Tables the card_access probes read.")


@pytest.mark.parametrize(
    ("change", "status", "detail"),
    [
        ({"connector": "nope"}, 422, "the entry is for nope.wallets"),
        ({"label": "transaction"}, 422, "a transaction table needs a time column"),
        ({"label": "unknown"}, 422, "label"),
    ],
)
def test_an_entry_that_is_not_valid_is_refused_before_anything_is_written(
    folder: Folder, change: dict[str, str], status: int, detail: str
) -> None:
    before = folder.catalog()
    response = confirm(folder, entry=WALLETS | change)
    assert response.status_code == status
    assert detail in str(response.json())
    assert folder.catalog() == before


def test_a_connector_the_application_does_not_have_is_refused(folder: Folder) -> None:
    entry = WALLETS | {"connector": "access_db"}
    response = folder.api.put(
        "/api/applications/wallet/catalog/tables/access_db/wallets", json={"entry": entry}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "wallet has no connector access_db"


def test_a_catalog_file_that_is_a_link_is_refused(folder: Folder, tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    path = folder.root / "wallet" / "catalog.yaml"
    outside.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    link(outside, path)
    folder.applications.update(load_applications(folder.root))
    response = confirm(folder)
    assert response.status_code == 422
    assert "symbolic link" in response.json()["detail"] or "outside" in response.json()["detail"]
    assert "wallets" not in outside.read_text(encoding="utf-8")


def test_a_catalog_file_holding_other_documents_is_refused(folder: Folder) -> None:
    path = folder.root / "wallet" / "catalog.yaml"
    path.write_text(folder.catalog() + "---\nprobe: other\n", encoding="utf-8")
    response = confirm(folder)
    assert response.status_code == 422
    assert "holds more than the catalog" in response.json()["detail"]


def test_without_a_config_folder_nothing_can_be_written(folder: Folder) -> None:
    catalog = CatalogService(folder.applications, folder.store)
    api = local_client(create_app(None, catalog))
    response = api.put(URL, json={"entry": WALLETS})
    assert response.status_code == 422
    assert "start owl serve with --config" in response.json()["detail"]


def test_a_confirm_whose_audit_cannot_be_written_is_refused_and_put_back(
    folder: Folder, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = folder.catalog()
    preview = confirm(folder).json()
    with monkeypatch.context() as patch:
        audit_fails(patch, probeowl.catalog.service, "catalog_table_confirmed")
        assert confirm(folder, preview["base"]).status_code == 500
    assert folder.catalog() == before
    assert folder.events() == []
    assert confirm(folder, preview["base"]).status_code == 200  # the same preview, once it can
