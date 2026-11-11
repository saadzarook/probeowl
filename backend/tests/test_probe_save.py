# Author: @SaadZarook
"""Saving a probe from the editor into YAML, under the rules for writing from the web."""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import probeowl.checks.saving
from probeowl.api.app import create_app
from probeowl.checks.saving import ProbeWrites
from probeowl.config_folder import ConfigFolder
from probeowl.connectors.plan import Access, PlanStep
from probeowl.model.check import check_folder, load_applications
from probeowl.runs import RunService
from probeowl.store.audit import list_events
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.audit_fails import audit_fails
from tests.local_client import local_client
from tests.plan_fakes import Plans
from tests.test_config_folder import link
from tests.test_runs import finished
from tests.wallet_fakes import Fake, connectors

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
URL = "/api/applications/wallet/probes/topup_completed"
NEW_URL = "/api/applications/wallet/probes/topup_captured"
NEW = """# When a top-up was captured.
probe: topup_captured
kind: validate
entity: topup
owner: payments-team
expect:
  - count(webhook) >= 1
"""


class Folder:
    """A copy of the examples behind the API, with fake plans and fake connectors."""

    def __init__(self, tmp_path: Path, plans: Plans | None = None):
        self.root = tmp_path / "config"
        shutil.copytree(EXAMPLES, self.root)
        self.path = self.root / "wallet" / "probes" / "topup_completed.yaml"
        self.text = self.path.read_text(encoding="utf-8")
        self.store = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
        upgrade(self.store)
        self.applications = load_applications(self.root)
        self.plans = plans or Plans()
        fakes = connectors()

        def opener(connector: Any, environment: str) -> Fake | None:
            return fakes.get(connector.connector)  # type: ignore[return-value]

        writes = ProbeWrites(
            self.applications,
            self.store,
            ConfigFolder(self.root),
            self.plans,
            actor_name="s.khan",
        )
        runs = RunService(self.applications, self.store, opener=opener, actor_name="s.khan")
        self.api = local_client(create_app(runs, plans=self.plans, probes=writes))

    def events(self) -> list[Any]:
        with self.store.connect() as conn:
            return [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "probe_saved"]

    def save(self, text: str, base: str | None = None, url: str = URL, env: str = "prod") -> Any:
        body = {"probe": text, "environment": env} | ({"base": base} if base else {})
        return self.api.put(url, json=body)


@pytest.fixture
def folder(tmp_path: Path) -> Folder:
    return Folder(tmp_path)


def edited(folder: Folder) -> str:
    return folder.text.replace("count(webhook) >= 1", "count(webhook) >= 2")


# --- Diff first, then exactly that ------------------------------------------------------------


def test_without_base_the_change_is_shown_and_nothing_is_written(folder: Folder) -> None:
    response = folder.save(edited(folder))
    assert response.status_code == 200, response.text
    change = response.json()
    assert change["written"] is False
    assert change["path"] == "wallet/probes/topup_completed.yaml"
    assert "-  - expect: count(webhook) >= 1\n+  - expect: count(webhook) >= 2\n" in change["diff"]
    assert change["report"]["can_save"] is True
    assert folder.path.read_text(encoding="utf-8") == folder.text
    assert folder.events() == []


def test_with_the_preview_base_exactly_that_change_is_written_and_audited(folder: Folder) -> None:
    preview = folder.save(edited(folder)).json()
    response = folder.save(edited(folder), preview["base"])
    assert response.status_code == 200, response.text
    written = response.json()
    assert written["written"] is True and written["diff"] == preview["diff"]
    assert folder.path.read_text(encoding="utf-8") == edited(folder)
    assert check_folder(folder.root).findings == []
    [event] = folder.events()
    assert (event.actor.name, event.application, event.environment) == ("s.khan", "wallet", "prod")
    assert event.details == {
        "path": "wallet/probes/topup_completed.yaml",
        "probe": "topup_completed",
        "created": False,
        "base": preview["base"],
        "after": written["after"],
    }


def test_the_saved_probe_is_the_one_that_runs_without_a_restart(folder: Folder) -> None:
    preview = folder.save(edited(folder)).json()
    folder.save(edited(folder), preview["base"])
    started = folder.api.post(
        "/api/runs",
        json={
            "application": "wallet",
            "environment": "prod",
            "probe": "topup_completed",
            "value": "tp_91f2b9",
            "investigation": {"reason": "incident", "reference": "WALLET-482"},
        },
    )
    run = finished(folder.api, started.json()["id"])
    assert "count(webhook) >= 2" in [e["expectation"] for e in run["expectations"]]


def test_a_file_changed_since_the_preview_is_a_409_and_not_written(folder: Folder) -> None:
    preview = folder.save(edited(folder)).json()
    hand_edited = folder.text + "# edited by hand\n"
    folder.path.write_text(hand_edited, encoding="utf-8")
    response = folder.save(edited(folder), preview["base"])
    assert response.status_code == 409
    assert "changed since its preview" in response.json()["detail"]
    assert folder.path.read_text(encoding="utf-8") == hand_edited
    assert folder.events() == []


def test_a_wrong_base_never_writes(folder: Folder) -> None:
    assert folder.save(edited(folder), "not-the-base").status_code == 409
    assert folder.path.read_text(encoding="utf-8") == folder.text


def test_saving_what_is_already_there_writes_and_audits_nothing(folder: Folder) -> None:
    preview = folder.save(folder.text).json()
    assert preview["diff"] == ""
    response = folder.save(folder.text, preview["base"]).json()
    assert response["written"] is False
    assert folder.events() == []


def test_comments_in_the_yaml_are_kept_as_typed(folder: Folder) -> None:
    text = edited(folder) + "# a note for reviewers\n"
    folder.save(text, folder.save(text).json()["base"])
    assert folder.path.read_text(encoding="utf-8").endswith("# a note for reviewers\n")


# --- A new probe --------------------------------------------------------------------------------


def test_a_new_probe_gets_a_file_in_its_applications_probes_folder(folder: Folder) -> None:
    preview = folder.save(NEW, url=NEW_URL).json()
    assert (preview["base"], preview["path"]) == ("absent", "wallet/probes/topup_captured.yaml")
    written = folder.save(NEW, preview["base"], NEW_URL).json()
    assert written["written"] is True
    assert (folder.root / preview["path"]).read_text(encoding="utf-8") == NEW
    assert check_folder(folder.root).findings == []
    [event] = folder.events()
    assert event.details["created"] is True
    # Listed and runnable at once.
    names = [p["name"] for p in folder.api.get("/api/applications/wallet/probes").json()]
    assert "topup_captured" in names
    # Reloaded from disk, it is the same probe.
    assert "topup_captured" in load_applications(folder.root)["wallet"].probes


def test_a_new_probe_never_overwrites_another_file_of_that_name(folder: Folder) -> None:
    other = folder.root / "wallet" / "probes" / "topup_captured.yaml"
    other.write_text("# someone's notes\n", encoding="utf-8")
    response = folder.save(NEW, url=NEW_URL)
    assert response.status_code == 422
    assert "holds more than probe topup_captured" in response.json()["detail"]
    assert other.read_text(encoding="utf-8") == "# someone's notes\n"


def test_a_probe_cannot_be_renamed_by_saving(folder: Folder) -> None:
    response = folder.save(NEW)  # the URL says topup_completed
    assert response.status_code == 422
    assert response.json()["detail"] == "the YAML is for probe topup_captured, not topup_completed"


def test_a_file_with_other_documents_is_refused(folder: Folder) -> None:
    folder.path.write_text(folder.text + "---\n" + NEW, encoding="utf-8")
    response = folder.save(edited(folder))
    assert response.status_code == 422
    assert "holds more than probe topup_completed" in response.json()["detail"]


# --- A probe that fails a save check cannot be written ------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("count(webhook) >= 1", "count(nope) >= 1"), "reads nope, not a location of topup"),
        (("entity: topup", "entity: refund"), "wallet has no entity refund"),
        (
            ("owner: payments-team", "owner: payments-team\nenvironments: [staging]"),
            "not environments of wallet: staging",
        ),
    ],
)
@pytest.mark.parametrize("with_base", [False, True])
def test_a_probe_that_fails_a_check_is_refused_at_preview_and_at_write(
    folder: Folder, change: tuple[str, str], message: str, with_base: bool
) -> None:
    good_base = folder.save(folder.text).json()["base"]
    text = folder.text.replace(*change)
    response = folder.save(text, good_base if with_base else None)
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert folder.path.read_text(encoding="utf-8") == folder.text
    assert folder.events() == []


def test_a_query_on_a_transaction_table_without_a_time_filter_blocks_saving(tmp_path: Path) -> None:
    # The loaded entity's lookup fails its label rule, so its probes cannot be saved.
    folder = Folder(tmp_path)
    entity = folder.root / "wallet" / "entities" / "topup.yaml"
    bad = "FROM provider_webhooks WHERE event_type = :provider_payment_id"
    entity.write_text(
        entity.read_text(encoding="utf-8").replace(
            "FROM provider_webhooks WHERE payment_id = :provider_payment_id", bad
        ),
        encoding="utf-8",
    )
    _reloaded(tmp_path, folder)
    response = folder.save(edited(folder))
    assert response.status_code == 422
    assert (
        "provider_webhooks is a Log table: filter on its time column received_at"
        in (response.json()["detail"])
    )
    fields = {e["field"] for e in response.json()["errors"]}
    assert "Time filter: webhook" in fields


def _reloaded(tmp_path: Path, folder: Folder) -> Folder:
    """The same folder, loaded again after a test changed its files."""
    folder.applications.clear()
    folder.applications.update(load_applications(folder.root))
    return folder


def test_a_full_scan_in_the_plan_blocks_saving(tmp_path: Path) -> None:
    def scans(sql: str) -> list[PlanStep]:
        return [PlanStep("Clustered Index Scan", Access.SCAN, "topups", "pk", 48_000_000)]

    folder = Folder(tmp_path, Plans(scans))
    response = folder.save(edited(folder))
    assert response.status_code == 422
    assert "the lookup would read all of topups" in response.json()["detail"]
    assert folder.path.read_text(encoding="utf-8") == folder.text


def test_the_checks_run_again_at_the_write(tmp_path: Path) -> None:
    # Passed at the preview; by the write, the index is gone.
    plans = Plans()
    folder = Folder(tmp_path, plans)
    preview = folder.save(edited(folder)).json()

    def scans(sql: str) -> list[PlanStep]:
        return [PlanStep("Table Scan", Access.SCAN, "topups", None, 48_000_000)]

    plans.planner = scans
    response = folder.save(edited(folder), preview["base"])
    assert response.status_code == 422
    assert folder.path.read_text(encoding="utf-8") == folder.text


def test_a_plan_that_cannot_be_read_warns_and_does_not_block_outside_production(
    folder: Folder,
) -> None:
    folder.plans.unbound = frozenset({"ledger_db"})
    preview = folder.save(edited(folder), env="uat").json()
    assert preview["report"]["warnings"] >= 1
    assert preview["report"]["plans_not_run"] >= 1
    assert folder.save(edited(folder), preview["base"], env="uat").json()["written"] is True
    [event] = folder.events()
    assert "saved_without_plan_check" not in event.details


def test_in_production_it_needs_the_acknowledgement_and_the_audit_says_who(
    folder: Folder,
) -> None:
    folder.plans.unbound = frozenset({"ledger_db"})
    preview = folder.save(edited(folder)).json()  # a preview shows the warning and refuses nothing
    assert preview["report"]["plans_not_run"] >= 1
    refused = folder.save(edited(folder), preview["base"])
    assert refused.status_code == 422
    assert "to save without plan check" in refused.json()["detail"]
    assert "ledger_db has no binding in prod" in refused.json()["detail"]
    assert [e["field"] for e in refused.json()["errors"]] == ["save_without_plan_check"]
    assert folder.path.read_text(encoding="utf-8") == folder.text
    assert folder.events() == []

    acknowledged = folder.api.put(
        URL,
        json={
            "probe": edited(folder),
            "environment": "prod",
            "base": preview["base"],
            "save_without_plan_check": True,
        },
    )
    assert acknowledged.json()["written"] is True
    [event] = folder.events()
    assert event.actor.name == "s.khan"
    assert event.details["saved_without_plan_check"] is True
    assert "ledger_db has no binding in prod" in event.details["plans_not_run"][0]


def test_an_acknowledgement_when_the_plans_ran_is_not_recorded(folder: Folder) -> None:
    preview = folder.save(edited(folder)).json()
    assert preview["report"]["plans_not_run"] == 0
    folder.api.put(
        URL,
        json={
            "probe": edited(folder),
            "environment": "prod",
            "base": preview["base"],
            "save_without_plan_check": True,
        },
    )
    [event] = folder.events()
    assert "saved_without_plan_check" not in event.details


def test_an_environment_is_required_so_plans_cannot_be_skipped(folder: Folder) -> None:
    response = folder.api.put(URL, json={"probe": edited(folder)})
    assert response.status_code == 422
    assert folder.save(edited(folder), env="staging").status_code == 404


# --- Inside the loaded folder only; never committed -------------------------------------------


def test_a_probe_file_that_is_a_link_is_refused(tmp_path: Path) -> None:
    folder = Folder(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text(folder.text, encoding="utf-8")
    folder.path.unlink()
    link(outside, folder.path)
    _reloaded(tmp_path, folder)
    response = folder.save(edited(folder))
    assert response.status_code == 422
    assert "symbolic link" in response.json()["detail"]
    assert outside.read_text(encoding="utf-8") == folder.text


def test_a_probes_folder_that_is_a_link_is_refused_for_a_new_probe(tmp_path: Path) -> None:
    folder = Folder(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # Moved aside, so loading still finds the existing probes under another name.
    probes = folder.root / "wallet" / "probes"
    probes.rename(folder.root / "wallet" / "saved_probes")
    link(elsewhere, probes)
    _reloaded(tmp_path, folder)
    response = folder.save(NEW, url=NEW_URL)
    assert response.status_code == 422
    assert "symbolic link" in response.json()["detail"]
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    ["..%2F..%2Fescape", "..\\..\\escape", "Not-A-Name"],
)
def test_a_probe_name_cannot_steer_the_path(folder: Folder, name: str) -> None:
    response = folder.save(NEW, url=f"/api/applications/wallet/probes/{name}")
    assert response.status_code in (404, 405, 422)
    assert not list(folder.root.parent.glob("escape*"))
    assert not (folder.root / "wallet" / "probes" / "topup_captured.yaml").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_in_a_git_checkout_a_saved_probe_is_left_for_a_person_to_commit(folder: Folder) -> None:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(folder.root), *args], check=True, capture_output=True, text=True
        ).stdout

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")
    folder.save(edited(folder), folder.save(edited(folder)).json()["base"])
    assert git("rev-list", "--count", "HEAD").strip() == "1"
    assert "M wallet/probes/topup_completed.yaml" in git("status", "--porcelain")


def test_without_a_config_folder_saving_answers_501(folder: Folder) -> None:
    api = local_client(create_app(None))
    assert api.put(URL, json={"probe": NEW, "environment": "prod"}).status_code == 501


def test_a_save_whose_audit_cannot_be_written_is_refused_and_put_back(
    folder: Folder, monkeypatch: pytest.MonkeyPatch
) -> None:
    preview = folder.save(edited(folder)).json()
    with monkeypatch.context() as patch:
        audit_fails(patch, probeowl.checks.saving, "probe_saved")
        assert folder.save(edited(folder), preview["base"]).status_code == 500
    assert folder.path.read_text(encoding="utf-8") == folder.text  # put back
    assert folder.events() == []
    # Nothing was left half-saved: the same preview saves once the store can be written.
    assert folder.save(edited(folder), preview["base"]).status_code == 200
    assert len(folder.events()) == 1
