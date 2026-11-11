# Author: @SaadZarook
"""A new entity saved on its own, as the entity wizard saves it: preview, then exactly that."""

from pathlib import Path
from typing import Any

import pytest

import probeowl.checks.saving
from probeowl.checks.plans import Access, PlanStep
from probeowl.model.check import check_folder
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.audit_fails import audit_fails
from tests.plan_fakes import Plans
from tests.test_probe_save import Folder

URL = "/api/applications/wallet/entities/topup_ledger"
# Two systems: the top-up in wallet_db, its ledger entries in ledger_db, by the external key.
ENTITY = """entity: topup_ledger
title: Top-up ledger
root_key: topup_id
owner: payments-team
identifiers:
  topup_id:
    unique: true
locations:
  topups:
    title: Topups
    using: wallet_db
    lookup: SELECT id AS topup_id, status, ledger_txn_id FROM topups WHERE id = :topup_id
    exposes: [topup_id, status, ledger_txn_id]
  ledger_entries:
    title: Ledger entries
    using: ledger_db
    key_from: topups.ledger_txn_id
    lookup: >
      SELECT txn_id, account, direction, amount
      FROM ledger_entries WHERE txn_id = :ledger_txn_id
    cardinality: many
    max_rows: 100
    exposes: [txn_id, account, direction, amount]
"""


class Wizard(Folder):
    """The examples behind the API, saving entities as the wizard does."""

    @property
    def target(self) -> Path:
        return self.root / "wallet" / "entities" / "topup_ledger.yaml"

    def create(
        self, text: str = ENTITY, base: str | None = None, url: str = URL, **more: Any
    ) -> Any:
        body = {"entity": text, "environment": "uat"} | more | ({"base": base} if base else {})
        return self.api.put(url, json=body)

    def created(self) -> list[Any]:
        with self.store.connect() as conn:
            return [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "entity_created"]


@pytest.fixture
def wizard(tmp_path: Path) -> Wizard:
    return Wizard(tmp_path)


def test_the_preview_shows_the_new_file_and_its_checks_and_writes_nothing(wizard: Wizard) -> None:
    response = wizard.create()
    assert response.status_code == 200, response.text
    change = response.json()
    assert change["path"] == "wallet/entities/topup_ledger.yaml"
    assert change["base"] == "absent" or len(change["base"]) == 64
    assert "+    key_from: topups.ledger_txn_id" in change["diff"]
    assert change["written"] is False
    assert change["report"]["entity"] == "topup_ledger"
    assert change["report"]["can_save"] is True
    # The index check ran on both systems' lookups.
    assert {c for c, _, _ in wizard.plans.asked} == {"wallet_db", "ledger_db"}
    assert not wizard.target.exists()
    assert wizard.created() == []


def test_confirming_the_preview_writes_it_loads_it_and_audits_it(wizard: Wizard) -> None:
    preview = wizard.create().json()
    written = wizard.create(base=preview["base"])
    assert written.status_code == 200, written.text
    assert written.json()["written"] is True
    assert wizard.target.read_text(encoding="utf-8") == ENTITY
    assert check_folder(wizard.root).findings == []
    # owl serve has it at once: the Probe editor can write its first probe.
    found = wizard.api.get("/api/applications/wallet/entities/topup_ledger")
    assert found.status_code == 200
    assert found.json()["locations"]["ledger_entries"]["key_from"] == "topups.ledger_txn_id"
    [event] = wizard.created()
    assert event.actor.name == "s.khan"
    assert event.details["entity"] == "topup_ledger"
    assert event.details["path"] == "wallet/entities/topup_ledger.yaml"


def test_a_lookup_that_writes_is_refused_and_nothing_is_written(wizard: Wizard) -> None:
    text = ENTITY.replace(
        "SELECT id AS topup_id, status, ledger_txn_id FROM topups WHERE id = :topup_id",
        "UPDATE topups SET status = 'x' WHERE id = :topup_id",
    )
    response = wizard.create(text)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert not wizard.target.exists()


def test_a_lookup_that_would_scan_its_table_is_refused(tmp_path: Path) -> None:
    def scans(sql: str) -> list[PlanStep]:
        return [PlanStep("Table Scan", Access.SCAN, "ledger_entries", None, 9_000_000)]

    wizard = Wizard(tmp_path, Plans(scans))
    response = wizard.create()
    assert response.status_code == 422
    assert "would read all of ledger_entries" in response.json()["detail"]
    assert not wizard.target.exists()


def test_an_entity_that_exists_is_changed_with_its_probes_instead(wizard: Wizard) -> None:
    text = ENTITY.replace("entity: topup_ledger", "entity: topup")
    response = wizard.create(text, url="/api/applications/wallet/entities/topup")
    assert response.status_code == 422
    assert "already has an entity topup" in response.json()["detail"]
    assert [e["field"] for e in response.json()["errors"]] == ["entity"]


def test_the_yaml_must_name_the_entity_being_saved(wizard: Wizard) -> None:
    response = wizard.create(url="/api/applications/wallet/entities/other")
    assert response.status_code == 422
    assert "is for entity topup_ledger, not other" in response.json()["detail"]


def test_an_unknown_connector_is_refused(wizard: Wizard) -> None:
    response = wizard.create(ENTITY.replace("using: ledger_db", "using: ledgr_db"))
    assert response.status_code == 422
    assert "no connector named ledgr_db" in response.json()["detail"]


def test_yaml_that_cannot_be_read_is_refused(wizard: Wizard) -> None:
    response = wizard.create("entity: [unclosed")
    assert response.status_code == 422
    assert not wizard.target.exists()


def test_a_file_that_appeared_since_the_preview_is_never_written_over(wizard: Wizard) -> None:
    preview = wizard.create().json()
    wizard.target.write_text("# someone else's\n", encoding="utf-8")
    response = wizard.create(base=preview["base"])
    assert response.status_code == 422
    assert "move the entity to its own file" in response.json()["detail"]
    assert wizard.target.read_text(encoding="utf-8") == "# someone else's\n"


def test_in_production_a_plan_that_could_not_be_read_needs_the_acknowledgement(
    wizard: Wizard,
) -> None:
    wizard.plans.unbound = frozenset({"ledger_db"})
    preview = wizard.create(environment="prod").json()
    assert preview["report"]["plans_not_run"] >= 1
    refused = wizard.create(base=preview["base"], environment="prod")
    assert refused.status_code == 422
    assert [e["field"] for e in refused.json()["errors"]] == ["save_without_plan_check"]
    assert not wizard.target.exists()
    acknowledged = wizard.create(
        base=preview["base"], environment="prod", save_without_plan_check=True
    )
    assert acknowledged.json()["written"] is True
    [event] = wizard.created()
    assert event.details["saved_without_plan_check"] is True


def test_an_entity_whose_audit_cannot_be_written_is_refused_and_not_left_behind(
    wizard: Wizard, monkeypatch: pytest.MonkeyPatch
) -> None:
    preview = wizard.create().json()
    with monkeypatch.context() as patch:
        audit_fails(patch, probeowl.checks.saving, "entity_created")
        assert wizard.create(base=preview["base"]).status_code == 500
    assert not wizard.target.exists()  # the new file was removed again
    assert wizard.created() == []
    assert "topup_ledger" not in wizard.applications["wallet"].entities
