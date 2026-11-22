# Author: @SaadZarook
"""An entity that exists, saved on its own (#161): checked with each of its probes, then written."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from probeowl.model.check import check_folder, load_applications
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.test_entity_save import _without_location
from tests.test_probe_save import Folder

URL = "/api/applications/wallet/entities/topup"
RENAMED = ("title: Top-up\n", "title: Wallet top-up\n")
# A new location on a connector that is bound in one environment only.
AUDIT = """  audit:
    title: Audit trail
    using: audit_db
    key_from: record.topup_id
    lookup: SELECT event, at FROM topup_audit WHERE topup_id = :topup_id
    cardinality: many
    max_rows: 20

"""


class Existing(Folder):
    """The examples behind the API, with the topup entity saved on its own."""

    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path)
        self.entity_path = self.root / "wallet" / "entities" / "topup.yaml"
        self.entity_text = self.entity_path.read_text(encoding="utf-8")
        self.edited = self.entity_text.replace(*RENAMED)

    def save_entity(
        self, text: str | None = None, base: str | None = None, environment: str = "uat"
    ) -> Any:
        body = {"entity": text or self.edited, "environment": environment}
        return self.api.put(URL, json=body | ({"base": base} if base else {}))

    def saved(self) -> list[Any]:
        with self.store.connect() as conn:
            return [
                e
                for e in list_events(conn, LOCAL_WORKSPACE)
                if e.action in {"entity_saved", "entity_created"}
            ]

    def unchanged(self) -> bool:
        return self.entity_path.read_text(encoding="utf-8") == self.entity_text

    def bind_audit_db_only_in(self, environment: str) -> str:
        """Add connector audit_db, bound in environment only; the entity with a location on it."""
        connectors = self.root / "wallet" / "connectors"
        head, bindings = (
            (connectors / "wallet_db.yaml").read_text(encoding="utf-8").split("environments:\n")
        )
        prod, uat = bindings.split("  uat:\n")
        binding = prod if environment == "prod" else f"  uat:\n{uat}"
        text = f"{head.replace('connector: wallet_db', 'connector: audit_db')}environments:\n"
        (connectors / "audit_db.yaml").write_text(text + binding, encoding="utf-8")
        self.applications.clear()
        self.applications.update(load_applications(self.root))
        return self.entity_text.replace("# Recent top-ups", f"{AUDIT}# Recent top-ups")


@pytest.fixture
def existing(tmp_path: Path) -> Existing:
    return Existing(tmp_path)


def test_the_preview_diffs_against_the_file_and_writes_nothing(existing: Existing) -> None:
    response = existing.save_entity()
    assert response.status_code == 200, response.text
    change = response.json()
    assert change["path"] == "wallet/entities/topup.yaml"
    assert len(change["base"]) == 64  # the file's hash, not "absent"
    assert "-title: Top-up\n+title: Wallet top-up" in change["diff"]
    assert "+entity: topup" not in change["diff"]  # only what changed
    assert change["written"] is False
    assert change["report"]["entity"] == "topup"
    assert change["report"]["can_save"] is True
    assert existing.unchanged()
    assert existing.saved() == []


def test_confirming_writes_it_serves_it_and_audits_it_as_a_change(existing: Existing) -> None:
    preview = existing.save_entity().json()
    written = existing.save_entity(base=preview["base"])
    assert written.status_code == 200, written.text
    assert written.json()["written"] is True
    assert existing.entity_path.read_text(encoding="utf-8") == existing.edited
    assert check_folder(existing.root).findings == []
    # owl serve has it at once, and its probe still runs on it.
    found = existing.api.get("/api/applications/wallet/entities/topup")
    assert found.json()["title"] == "Wallet top-up"
    [event] = existing.saved()
    assert event.action == "entity_saved"
    assert event.actor.name == "s.khan"
    assert event.details["created"] is False
    assert event.details["base"] == preview["base"]
    assert event.details["path"] == "wallet/entities/topup.yaml"


def test_a_change_that_breaks_one_of_its_probes_is_refused_naming_it(existing: Existing) -> None:
    # topup_completed expects count(webhook) >= 1.
    response = existing.save_entity(_without_location(existing.entity_text, "webhook"))
    assert response.status_code == 422
    assert response.json()["detail"].startswith(
        "the draft does not pass its save checks: probe topup_completed:"
    )
    assert existing.unchanged()
    assert existing.saved() == []


def test_the_file_changing_since_the_preview_is_a_conflict(existing: Existing) -> None:
    preview = existing.save_entity().json()
    by_hand = f"{existing.entity_text}# changed by hand\n"
    existing.entity_path.write_text(by_hand, encoding="utf-8")
    response = existing.save_entity(base=preview["base"])
    assert response.status_code == 409
    assert "changed since its preview" in response.json()["detail"]
    assert existing.entity_path.read_text(encoding="utf-8") == by_hand
    assert existing.saved() == []


def test_a_location_unbound_outside_production_warns_with_its_probe_named(
    existing: Existing,
) -> None:
    text = existing.bind_audit_db_only_in("prod")
    response = existing.save_entity(text)
    assert response.status_code == 200, response.text
    report = response.json()["report"]
    assert report["can_save"] is True
    [warning] = [c for c in report["checks"] if c["check"] == "Environments"]
    assert warning["status"] == "warn"
    assert warning["message"].startswith(
        "probe topup_completed: enabled in uat, where audit (audit_db) has no binding: "
        "it is Not configured there"
    )
    # About that probe, not the one an editor has open: no location or environment to act on.
    assert warning["location"] is None
    assert warning["environment"] is None


def test_a_location_unbound_in_production_is_refused_with_its_probe_named(
    existing: Existing,
) -> None:
    text = existing.bind_audit_db_only_in("uat")
    response = existing.save_entity(text)
    assert response.status_code == 422
    assert (
        "probe topup_completed: enabled in prod, where audit (audit_db) has no binding"
        in (response.json()["detail"])
    )
    assert existing.unchanged()


def test_a_file_with_other_documents_is_refused(existing: Existing) -> None:
    existing.entity_path.write_text(
        f"{existing.entity_text.rstrip()}\n---\n{existing.entity_text}", encoding="utf-8"
    )
    response = existing.save_entity()
    assert response.status_code == 422
    assert "holds more than entity topup" in response.json()["detail"]


def _a_second_probe_on_webhook(existing: Existing) -> None:
    other = existing.root / "wallet" / "probes" / "topup_webhooked.yaml"
    text = existing.text.replace("probe: topup_completed", "probe: topup_webhooked")
    other.write_text(text, encoding="utf-8")
    existing.applications.clear()
    existing.applications.update(load_applications(existing.root))


def test_the_preview_lists_every_probe_that_would_fail(existing: Existing) -> None:
    _a_second_probe_on_webhook(existing)
    # The preview, with no base: refused, each failing probe its own entry.
    response = existing.save_entity(_without_location(existing.entity_text, "webhook"))
    assert response.status_code == 422
    messages = [e["message"] for e in response.json()["errors"]]
    assert any(m.startswith("probe topup_completed: ") for m in messages), messages
    assert any(m.startswith("probe topup_webhooked: ") for m in messages), messages
    assert len({e["field"] for e in response.json()["errors"]}) == len(messages)
    assert existing.unchanged()
    assert existing.saved() == []


def test_a_probe_fixed_in_the_same_change_lets_the_entity_save(existing: Existing) -> None:
    entity = _without_location(existing.entity_text, "webhook")
    # On its own, the change breaks topup_completed, which expects count(webhook) >= 1 ...
    assert existing.save_entity(entity).status_code == 422
    # ... saved with that probe fixed, the same change passes, and both files are written.
    probe = existing.text.replace("count(webhook) >= 1", "count(record) == 1")
    url = "/api/applications/wallet/probes/topup_completed"
    body = {"probe": probe, "entity": entity, "environment": "uat"}
    preview = existing.api.put(url, json=body)
    assert preview.status_code == 200, preview.text
    written = existing.api.put(url, json=body | {"base": preview.json()["base"]})
    assert written.status_code == 200, written.text
    assert "webhook:" not in existing.entity_path.read_text(encoding="utf-8")
    assert "count(record) == 1" in existing.path.read_text(encoding="utf-8")
    assert check_folder(existing.root).findings == []


def test_the_base_is_the_files_own_hash_as_for_every_write(existing: Existing) -> None:
    preview = existing.save_entity().json()
    assert preview["base"] == hashlib.sha256(existing.entity_path.read_bytes()).hexdigest()


def test_the_audit_records_who_where_and_both_hashes_and_no_lookup_or_data(
    existing: Existing,
) -> None:
    preview = existing.save_entity().json()
    existing.save_entity(base=preview["base"])
    [event] = existing.saved()
    assert event.action == "entity_saved"
    assert event.actor.name == "s.khan"
    assert event.application == "wallet"
    assert event.environment == "uat"
    assert event.run_id is None
    assert event.details == {
        "path": "wallet/entities/topup.yaml",
        "entity": "topup",
        "created": False,
        "base": preview["base"],
        "after": hashlib.sha256(existing.entity_path.read_bytes()).hexdigest(),
    }
    # Hashes, never the YAML: no lookup, no title, no diff.
    recorded = event.model_dump_json()
    for text in ("SELECT", "FROM topups", "Wallet top-up", "customer_email", "@@"):
        assert text not in recorded
