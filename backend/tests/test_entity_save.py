# Author: @SaadZarook
"""Saving an entity with its probe: one base covers both, and both are written or neither."""

from pathlib import Path
from typing import Any

import pytest

from probeowl import config_folder
from probeowl.model.check import CheckReport, Finding, check_folder, load_applications
from tests.test_probe_save import Folder

URL = "/api/applications/wallet/probes/topup_completed"
MARK = "# saved with its probe"


class Both(Folder):
    """A folder whose probe and entity are saved together."""

    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path)
        self.entity_path = self.root / "wallet" / "entities" / "topup.yaml"
        self.entity_text = self.entity_path.read_text(encoding="utf-8")
        self.edited_probe = self.text.replace(
            "count(webhook) >= 1", "count(webhook) >= 1  # with its entity"
        )
        self.edited_entity = f"{self.entity_text.rstrip()}\n{MARK}\n"

    def save_both(
        self, base: str | None = None, entity: str | None = None, probe: str | None = None
    ) -> Any:
        body = {
            "probe": probe or self.edited_probe,
            "entity": entity or self.edited_entity,
            "environment": "uat",
        } | ({"base": base} if base else {})
        return self.api.put(URL, json=body)

    def unchanged(self) -> bool:
        return (
            self.path.read_text(encoding="utf-8") == self.text
            and self.entity_path.read_text(encoding="utf-8") == self.entity_text
        )


@pytest.fixture
def both(tmp_path: Path) -> Both:
    return Both(tmp_path)


def test_the_preview_shows_both_files_and_writes_neither(both: Both) -> None:
    response = both.save_both()
    assert response.status_code == 200
    change = response.json()
    assert change["path"] == "wallet/probes/topup_completed.yaml"
    assert "# with its entity" in change["diff"]
    assert change["entity_change"]["path"] == "wallet/entities/topup.yaml"
    assert MARK in change["entity_change"]["diff"]
    assert not change["written"] and not change["entity_change"]["written"]
    # One base covers both: neither file's own hash confirms the write.
    assert change["base"] != change["entity_change"]["base"]
    assert both.unchanged()
    assert both.events() == []


def test_confirming_writes_both_and_audits_once(both: Both) -> None:
    base = both.save_both().json()["base"]
    change = both.save_both(base).json()
    assert change["written"] and change["entity_change"]["written"]
    assert MARK in both.entity_path.read_text(encoding="utf-8")
    assert "# with its entity" in both.path.read_text(encoding="utf-8")
    [event] = both.events()
    assert event.actor.name == "s.khan"
    assert event.details["entity"] == "topup"
    assert event.details["entity_path"] == "wallet/entities/topup.yaml"
    assert event.details["entity_after"] == change["entity_change"]["after"]
    assert event.details["entity_created"] is False


def test_the_saved_entity_is_the_one_that_runs_without_a_restart(both: Both) -> None:
    edited = both.entity_text.replace("max_rows: 20", "max_rows: 19")
    assert edited != both.entity_text
    probe_with = both.save_both(entity=edited).json()
    both.save_both(probe_with["base"], entity=edited)
    fresh = load_applications(both.root)["wallet"].entities["topup"]
    assert both.applications["wallet"].entities["topup"] == fresh


@pytest.mark.parametrize("changed", ["probe", "entity"])
def test_either_file_changing_since_the_preview_writes_neither(both: Both, changed: str) -> None:
    base = both.save_both().json()["base"]
    path = both.path if changed == "probe" else both.entity_path
    outside = path.read_text(encoding="utf-8") + "# edited by someone else\n"
    path.write_text(outside, encoding="utf-8", newline="")
    response = both.save_both(base)
    assert response.status_code == 409
    assert path.read_text(encoding="utf-8") == outside
    other = both.entity_path if changed == "probe" else both.path
    assert other.read_text(encoding="utf-8") == (
        both.entity_text if changed == "probe" else both.text
    )
    assert both.events() == []


def test_the_base_covers_the_change_as_well_as_the_files(both: Both) -> None:
    base = both.save_both().json()["base"]
    other = f"{both.entity_text.rstrip()}\n# something else\n"
    assert both.save_both(base, entity=other).status_code == 409
    assert both.unchanged()


def test_when_the_second_write_fails_the_first_is_put_back(
    both: Both, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = both.save_both().json()["base"]
    real = config_folder._replace

    def fails_on_the_entity(path: Path, content: str) -> None:
        if path.name == "topup.yaml":
            raise OSError("the disk is full")
        real(path, content)

    monkeypatch.setattr(config_folder, "_replace", fails_on_the_entity)
    assert both.save_both(base).status_code == 500
    assert both.unchanged()
    assert both.events() == []
    assert both.applications["wallet"].probes["topup_completed"].probe == "topup_completed"


def test_a_new_probe_is_removed_again_when_its_entity_cannot_be_written(
    both: Both, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = both.root / "wallet" / "probes" / "topup_new.yaml"
    probe = both.text.replace("probe: topup_completed", "probe: topup_new")
    url = "/api/applications/wallet/probes/topup_new"
    body = {"probe": probe, "entity": both.edited_entity, "environment": "uat"}
    base = both.api.put(url, json=body).json()["base"]
    real = config_folder._replace

    def fails_on_the_entity(path: Path, content: str) -> None:
        if path.name == "topup.yaml":
            raise OSError("the disk is full")
        real(path, content)

    monkeypatch.setattr(config_folder, "_replace", fails_on_the_entity)
    assert both.api.put(url, json=body | {"base": base}).status_code == 500
    assert not created.exists()


def test_when_putting_back_fails_the_person_is_told(
    both: Both, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = both.save_both().json()["base"]
    real = config_folder._replace
    calls = {"count": 0}

    def fails_after_the_first(path: Path, content: str) -> None:
        calls["count"] += 1
        if calls["count"] > 1:
            raise OSError("the disk is full")
        real(path, content)

    monkeypatch.setattr(config_folder, "_replace", fails_after_the_first)
    response = both.save_both(base)
    assert response.status_code == 422
    assert "topup_completed.yaml could not be put back" in response.json()["detail"]


def test_a_save_that_would_stop_the_folder_loading_is_undone(
    both: Both, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = both.save_both().json()["base"]
    report = CheckReport()
    broken = both.root / "wallet" / "x.yaml"
    report.findings.append(Finding(broken, "something no save check knows"))
    monkeypatch.setattr("probeowl.checks.saving.check_folder", lambda root: report)
    response = both.save_both(base)
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "saving would break the folder: wallet/x.yaml: something no save check knows"
    )
    assert response.json()["errors"] == [
        {"field": "wallet/x.yaml", "message": "something no save check knows"}
    ]
    assert both.unchanged()
    assert both.events() == []


def test_a_file_broken_by_hand_is_named_in_owl_checks_words(both: Both) -> None:
    base = both.save_both().json()["base"]
    broken = both.root / "wallet" / "connectors" / "broken.yaml"
    broken.write_text("connector: [not, a, connector\n", encoding="utf-8")
    [finding] = check_folder(both.root).findings
    response = both.save_both(base)
    assert response.status_code == 422
    relative = "wallet/connectors/broken.yaml"
    assert response.json()["detail"] == (
        f"saving would break the folder: {relative}: {finding.message}"
    )
    assert both.unchanged()


def test_an_entity_that_fails_a_check_writes_neither(both: Both) -> None:
    bad = both.entity_text.replace(
        "WHERE txn_id = :ledger_txn_id", "WHERE account = :ledger_txn_id"
    )
    assert bad != both.entity_text
    response = both.save_both(entity=bad)
    assert response.status_code == 422
    assert "ledger_entries is a Transaction table" in response.json()["detail"]
    assert both.unchanged()


def test_an_entity_for_another_name_is_refused(both: Both) -> None:
    other = both.entity_text.replace("entity: topup", "entity: refund")
    response = both.save_both(entity=other)
    assert response.status_code == 422
    assert "the probe is for topup, the draft entity is refund" in response.json()["detail"]


def test_an_entity_change_that_breaks_another_probe_is_refused(tmp_path: Path) -> None:
    both = Both(tmp_path)
    other = both.root / "wallet" / "probes" / "topup_ledger.yaml"
    other.write_text(
        both.text.replace("probe: topup_completed", "probe: topup_ledger"), encoding="utf-8"
    )
    both.applications.clear()
    both.applications.update(load_applications(both.root))
    # The edited probe stops using webhook; the other still does.
    probe = both.text.replace("count(webhook) >= 1", "count(record) == 1")
    entity = _without_location(both.entity_text, "webhook")
    response = both.save_both(entity=entity, probe=probe)
    assert response.status_code == 422
    assert "probe topup_ledger" in response.json()["detail"]
    assert both.unchanged()


def test_a_file_with_other_documents_is_refused(both: Both) -> None:
    both.entity_path.write_text(
        f"{both.entity_text.rstrip()}\n---\n{both.entity_text}", encoding="utf-8"
    )
    response = both.save_both()
    assert response.status_code == 422
    assert "holds more than entity topup" in response.json()["detail"]


def test_a_new_entity_gets_a_file_in_its_applications_entities_folder(both: Both) -> None:
    entity = both.entity_text.replace("entity: topup", "entity: refund")
    probe = both.text.replace("probe: topup_completed", "probe: refund_done").replace(
        "entity: topup", "entity: refund"
    )
    url = "/api/applications/wallet/probes/refund_done"
    body = {"probe": probe, "entity": entity, "environment": "uat"}
    preview = both.api.put(url, json=body).json()
    assert preview["entity_change"]["base"] == "absent"
    assert both.api.put(url, json=body | {"base": preview["base"]}).status_code == 200
    assert (both.root / "wallet" / "entities" / "refund.yaml").exists()
    [event] = both.events()
    assert event.details["entity_created"] is True


def test_a_probe_alone_keeps_the_files_own_hash_as_its_base(both: Both) -> None:
    change = both.save(both.edited_probe, env="uat").json()
    assert change["entity_change"] is None
    assert both.save(both.edited_probe, change["base"], env="uat").json()["written"]
    assert both.entity_path.read_text(encoding="utf-8") == both.entity_text


def _without_location(text: str, name: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.rstrip() == f"  {name}:")
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].strip() and not lines[i].startswith("   ")
        ),
        len(lines),
    )
    return "".join(lines[:start] + lines[end:])
