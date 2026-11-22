# Author: @SaadZarook
"""A location whose connector has no binding in the environment is Not configured (#25).

Outside Production the run goes on: the location is Not configured, those keyed from it are
Blocked, and the checks that read them are not applicable, so a run with nothing else wrong is
Passed with a note, and `--strict` exits 2. In Production it stays a mistake: refused when the
probe is saved, when owl serve loads it, and when a run starts.
"""

import dataclasses
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from probeowl.checks.rules import unbound_warnings
from probeowl.checks.service import check_draft
from probeowl.cli import main as cli
from probeowl.cli import server
from probeowl.errors import ConfigError
from probeowl.model.check import LoadedApplication, load_applications
from probeowl.model.save_check import CheckStatus
from probeowl.runs import _refuse_unbound
from tests.card_fakes import CARDS, VISITOR, access_db
from tests.conftest import EXAMPLES, Setup
from tests.test_probe_save import Folder
from tests.test_runs import body, finished, runs_stored, start
from tests.wallet_fakes import Fake

runner = CliRunner()
CONTROLLER = "door_controller_api"
VISITOR_CARDS = (EXAMPLES / "card-access" / "probes" / "visitor_cards.yaml").read_text(
    encoding="utf-8"
)
NOTE = "1 check not applicable in uat: door_controller_api has no binding here"


def _healthy_access_db(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
    # VP00012931 with only its one valid active card: every check that can run passes.
    if "FROM cards" in lookup:
        return [dict(c) for c in CARDS if c["card_id"] == "C204518"]
    return access_db(lookup, p)


@pytest.fixture
def cards(setup: Setup) -> Setup:
    """Card access with a healthy visitor, and the door controller not bound."""
    setup.fakes["access_db"] = Fake(_healthy_access_db)
    setup.unbound.add(CONTROLLER)
    return setup


def visitor_run(setup: Setup, environment: str = "uat") -> Any:
    record = {"reason": "incident", "reference": "SUP-20931"}
    changes = {"application": "card_access", "probe": "visitor_cards", "environment": environment}
    return body("VP00012931", **changes, investigation=record)


def _unbound(app: LoadedApplication, connector: str, environment: str) -> LoadedApplication:
    found = app.connectors[connector]
    kept = {k: v for k, v in found.environments.items() if k != environment}
    unbound = found.model_copy(update={"environments": kept})
    return dataclasses.replace(app, connectors={**app.connectors, connector: unbound})


# --- A run outside Production ---------------------------------------------------------------


def test_outside_production_the_run_passes_with_its_checks_not_applicable(cards: Setup) -> None:
    api = cards.client()
    run = finished(api, start(api, **visitor_run(cards)))
    assert run["status"] == "passed"
    controller = [r for r in run["locations"] if r["location"] == "controller"]
    assert [r["outcome"] for r in controller] == ["not_configured"]
    assert controller[0]["reason"] == "door_controller_api has no binding here"
    assert controller[0]["evidence"] is None  # nothing was looked up
    assert not cards.fakes[CONTROLLER].calls
    by_text = {e["expectation"]: e for e in run["expectations"]}
    agree = by_text["all(cards, controller.status == status)"]
    assert (agree["result"], agree["not_applicable"]) == ("skip", True)
    assert all(
        e["result"] == "pass" and e["not_applicable"] is False
        for text, e in by_text.items()
        if text != "all(cards, controller.status == status)"
    )
    assert run["not_applicable"] == [
        {"connector": CONTROLLER, "locations": ["cards.controller"], "checks": 1}
    ]


def test_a_finding_elsewhere_still_fails_the_run(setup: Setup) -> None:
    # The planted visitor: two of its card issues don't need the controller.
    setup.unbound.add(CONTROLLER)
    api = setup.client()
    run = finished(api, start(api, **visitor_run(setup)))
    assert run["status"] == "failed"
    assert run["not_applicable"][0]["checks"] == 1


def test_in_production_the_run_is_refused_before_any_lookup(cards: Setup) -> None:
    api = cards.client()
    refused = api.post("/api/runs", json=visitor_run(cards, "prod"))
    assert refused.status_code == 422
    assert "in Production every location must be bound" in refused.json()["detail"]
    assert "controller (door_controller_api)" in refused.json()["detail"]
    assert all(not fake.calls for fake in cards.fakes.values())
    assert runs_stored(cards) == 0


def test_an_unbound_root_is_refused_even_outside_production(cards: Setup) -> None:
    # Nothing could be found, so a Passed would say nothing.
    cards.unbound.add("access_db")
    refused = cards.client().post("/api/runs", json=visitor_run(cards))
    assert refused.status_code == 422
    assert (
        "its root location uses access_db, which has no binding here" in (refused.json()["detail"])
    )
    assert runs_stored(cards) == 0


def test_an_identifier_looked_up_through_an_unbound_connector_is_refused() -> None:
    cards_elsewhere = VISITOR.locations["cards"].model_copy(update={"using": "badge_db"})
    entity = VISITOR.model_copy(
        update={"locations": {**VISITOR.locations, "cards": cards_elsewhere}}
    )
    with pytest.raises(ConfigError, match=r"card_id is looked up through badge_db"):
        _refuse_unbound(entity, ("badge_db",), "UAT", False, "card_id")
    _refuse_unbound(entity, ("badge_db",), "UAT", False, "visitor_profile_id")  # the root is fine


# --- Saving, loading and owl check: the mistake is still caught (#95) -------------------------


def test_saving_a_probe_enabled_in_production_without_a_binding_is_refused(
    tmp_path: Path,
) -> None:
    folder = Folder(tmp_path)
    folder.applications["card_access"] = _unbound(
        folder.applications["card_access"], CONTROLLER, "prod"
    )
    response = folder.api.put(
        "/api/applications/card_access/probes/visitor_cards",
        json={"probe": VISITOR_CARDS, "environment": "uat"},
    )
    assert response.status_code == 422, response.text
    [error] = [e for e in response.json()["errors"] if e["check"] == "Environments"]
    assert error["location"] == "cards.controller"
    assert error["message"].startswith("enabled in prod, where cards.controller")
    assert "bind door_controller_api in prod" in error["message"]


def test_outside_production_the_save_check_warns_and_the_probe_can_be_saved() -> None:
    app = _unbound(load_applications(EXAMPLES)["card_access"], CONTROLLER, "uat")
    report = check_draft(app, VISITOR_CARDS)
    [check] = [c for c in report.checks if c.check == "Environments"]
    assert check.status is CheckStatus.WARN
    assert (check.location, check.environment) == ("cards.controller", "uat")
    assert "it is Not configured there" in check.message
    assert report.can_save


def _copy_without_binding(tmp_path: Path, environment: str) -> Path:
    root = tmp_path / "config"
    shutil.copytree(EXAMPLES, root)
    path = root / "card-access" / "connectors" / "door_controller_api.yaml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text(
        "".join(line for line in lines if not line.lstrip().startswith(f"{environment}:")),
        encoding="utf-8",
    )
    return root


def test_owl_serve_refuses_to_load_a_probe_unbound_in_production(tmp_path: Path) -> None:
    root = _copy_without_binding(tmp_path, "prod")
    with pytest.raises(ConfigError, match=r"enabled in prod, where location cards.controller"):
        load_applications(root)
    result = runner.invoke(cli.app, ["check", str(root), "--no-connect"])
    assert result.exit_code == 1
    assert "enabled in prod, where location cards.controller has no binding" in result.output


def test_outside_production_it_loads_and_owl_check_warns(tmp_path: Path) -> None:
    root = _copy_without_binding(tmp_path, "uat")
    app = load_applications(root)["card_access"]
    [warning] = unbound_warnings(app)
    assert warning.startswith(
        "card_access: probe visitor_cards: enabled in uat, where cards.controller"
    )
    result = runner.invoke(cli.app, ["check", str(root), "--no-connect"])
    assert result.exit_code == 0, result.output
    assert "warning: card_access: probe visitor_cards: enabled in uat" in result.output


# --- The CLI: the note, and --strict ---------------------------------------------------------


@pytest.fixture
def owl_api(cards: Setup, monkeypatch: pytest.MonkeyPatch) -> Setup:
    client = cards.client()
    monkeypatch.setattr(server, "client", lambda url: client)
    return cards


UAT = ["VP00012931", "--env", "uat", "--reason", "incident"]


def test_owl_inspect_notes_checks_not_applicable_and_exits_0(owl_api: Setup) -> None:
    result = runner.invoke(cli.app, ["inspect", *UAT])
    assert result.exit_code == 0, result.output
    out = result.output
    unbound = r"^  controller\[cards 1\] Not configured  door_controller_api has no binding here$"
    assert re.search(unbound, out, re.M)
    assert "  n/a  all(cards, controller.status == status)  (needs controller" in out
    assert f"note: {NOTE}" in out
    assert re.search(r"^Run [0-9a-f-]{36}: passed$", out, re.M)


def test_strict_makes_checks_not_applicable_exit_2(owl_api: Setup) -> None:
    result = runner.invoke(cli.app, ["inspect", *UAT, "--strict"])
    assert result.exit_code == 2, result.output
    assert f"note: {NOTE}" in result.output
    assert re.search(
        r"^Run [0-9a-f-]{36}: passed \(--strict: 1 check not applicable\)$", result.output, re.M
    )


def test_strict_changes_nothing_when_every_check_applies(setup: Setup, monkeypatch: Any) -> None:
    setup.fakes["access_db"] = Fake(_healthy_access_db)
    client = setup.client()
    monkeypatch.setattr(server, "client", lambda url: client)
    result = runner.invoke(cli.app, ["inspect", *UAT, "--strict"])
    assert result.exit_code == 0, result.output
    assert "note:" not in result.output


def test_owl_validate_notes_them_too(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    # The provider's API not bound in UAT: the payment, and the two checks that read it, don't
    # apply. A check skipped by its own `when` is not counted among them.
    setup.unbound.add("checkout_api")
    client = setup.client()
    monkeypatch.setattr(server, "client", lambda url: client)
    args = ["validate", "topup", "tp_91f2b9", "--env", "uat", "--reason", "incident"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    out = result.output
    assert re.search(r"^  payment\s+Not configured  checkout_api has no binding here$", out, re.M)
    assert "  n/a  payment.amount == record.amount  (needs payment" in out
    assert "  skip payment.status != 'captured'  (when record.status == 'failed' is false)" in out
    assert "note: 2 checks not applicable in uat: checkout_api has no binding here" in out
    strict = runner.invoke(cli.app, [*args, "--strict"])
    assert strict.exit_code == 2, strict.output
    assert re.search(r": passed \(--strict: 2 checks not applicable\)$", strict.output, re.M)
