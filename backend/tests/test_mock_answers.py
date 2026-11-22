# Author: @SaadZarook
"""The web's mocks say exactly what the backend says (#108).

Each file in web/mocks/fixtures/backend/ is the backend's answer to one named case, which the mocks
import as it is. Each case is built here for real, through the same route the web calls where it
has one, and the test fails if the answer differs from the file in message or shape. Only what a
database would answer is faked: EXPLAIN plans and lookups. After a deliberate change, write the
files again with PROBEOWL_WRITE_MOCK_ANSWERS=1 and review the diff.
"""

import dataclasses
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from probeowl.checks.service import check_draft
from probeowl.connectors.plan import Access, PlanStep
from probeowl.model.check import LoadedApplication, load_applications
from tests.conftest import Setup
from tests.plan_fakes import Plans
from tests.test_entity_create import Wizard
from tests.test_run_steps import REVIEWED_READ, failed_run, large_scan, outbox, step_url

REPOSITORY = Path(__file__).resolve().parents[2]
EXAMPLES = REPOSITORY / "examples"
ANSWERS = REPOSITORY / "web" / "mocks" / "fixtures" / "backend"

# The probe editor's draft entity that reads a Transaction table by a column that is no key.
TIME_FILTER_ENTITY = (
    "entity: topup\nroot_key: topup_id\nlocations: {record: {using: wallet_db, lookup: "
    "'SELECT id FROM topups WHERE status = :status'}}"
)
# The entity wizard's Link by columns: cards looked up by status, which no index covers.
CARD_STATUS_ENTITY = """entity: card_status
title: Card status
root_key: visitor_profile_id
owner: access-team
identifiers:
  visitor_profile_id:
    unique: true
locations:
  visitor_profile:
    title: Visitor profile
    using: access_db
    lookup: >
      SELECT id AS visitor_profile_id, status FROM visitor_profile
      WHERE id = :visitor_profile_id
    exposes: [visitor_profile_id, status]
  cards:
    title: Cards
    using: access_db
    key_from: visitor_profile.status
    lookup: SELECT card_id, status FROM cards WHERE status = :status
    cardinality: many
    max_rows: 50
    exposes: [card_id, status]
"""
# The entity wizard following wallets.user_id into topups, a Transaction table, by user_id.
WALLET_TOPUPS_ENTITY = """entity: wallet_topups
title: Wallet topups
root_key: user_id
owner: payments-team
identifiers:
  user_id:
    unique: true
locations:
  wallets:
    title: Wallets
    using: wallet_db
    lookup: SELECT user_id, balance FROM wallets WHERE user_id = :user_id
    exposes: [user_id, balance]
  topups:
    title: Topups
    using: wallet_db
    key_from: wallets.user_id
    lookup: SELECT id, user_id, status FROM topups WHERE user_id = :user_id
    cardinality: many
    max_rows: 50
    exposes: [id, user_id, status]
"""


def _unbound(app: LoadedApplication, connector: str, environment: str) -> LoadedApplication:
    found = app.connectors[connector]
    kept = {k: v for k, v in found.environments.items() if k != environment}
    unbound = found.model_copy(update={"environments": kept})
    return dataclasses.replace(app, connectors={**app.connectors, connector: unbound})


def _problem(response: Any) -> Any:
    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == "application/problem+json"
    return response.json()


def _step_refusal(setup: Setup, mock_run: str) -> Any:
    """The step's refusal on a failed run, its run id the mock's: a real one is new each time."""
    api = setup.client()
    run_id = failed_run(api)
    answer = _problem(api.post(step_url(run_id, REVIEWED_READ), json={}))
    answer["instance"] = answer["instance"].replace(run_id, mock_run)
    return answer


def save_checks_environment_unbound(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """visitor_cards in Card access, access_db unbound in Production (mock-prod-unbound)."""
    app = _unbound(load_applications(EXAMPLES)["card_access"], "access_db", "prod")
    probe = (EXAMPLES / "card-access" / "probes" / "visitor_cards.yaml").read_text(encoding="utf-8")
    return check_draft(app, probe).model_dump(mode="json")


def save_checks_passing(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """The probe editor's Save checks panel for topup_completed as it is: every check passes."""
    app = load_applications(EXAMPLES)["wallet"]
    probe = (EXAMPLES / "wallet" / "probes" / "topup_completed.yaml").read_text(encoding="utf-8")
    return check_draft(app, probe).model_dump(mode="json")


def save_checks_time_filter(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """The probe editor's Save checks panel for topup_completed on TIME_FILTER_ENTITY."""
    app = load_applications(EXAMPLES)["wallet"]
    probe = (EXAMPLES / "wallet" / "probes" / "topup_completed.yaml").read_text(encoding="utf-8")
    return check_draft(app, probe, TIME_FILTER_ENTITY).model_dump(mode="json")


def draft_run_time_filter(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """Run draft refused: topup_completed on TIME_FILTER_ENTITY, in Production."""
    setup: Setup = request.getfixturevalue("setup")
    probe = (EXAMPLES / "wallet" / "probes" / "topup_completed.yaml").read_text(encoding="utf-8")
    body = {
        "probe": probe,
        "entity": TIME_FILTER_ENTITY,
        "environment": "prod",
        "value": "tp_91f2c7",
        "investigation": {"reason": "incident", "reference": "WALLET-482"},
    }
    return _problem(setup.client().post("/api/applications/wallet/probes/test-runs", json=body))


def entity_save_plan_scan(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """The entity wizard's card_status, whose cards lookup would read the whole table."""

    def scans_cards(sql: str) -> list[PlanStep]:
        if "FROM cards" in sql:
            return [PlanStep("Table Scan", Access.SCAN, "cards", None, 2_000_000)]
        return [PlanStep("Index Seek", Access.INDEX, "visitor_profile", "pk_visitor_profile")]

    wizard = Wizard(tmp_path, Plans(scans_cards))
    url = "/api/applications/card_access/entities/card_status"
    return _problem(wizard.create(CARD_STATUS_ENTITY, url=url, environment="prod"))


def entity_save_time_filter(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """The entity wizard's wallet_topups: topups read by user_id, which the catalog calls no key."""
    wizard = Wizard(tmp_path)
    url = "/api/applications/wallet/entities/wallet_topups"
    return _problem(wizard.create(WALLET_TOPUPS_ENTITY, url=url, environment="prod"))


def step_plan_scan(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """Runbook step check-outbox refused: its plan reads all of a large table (run_scan)."""
    setup: Setup = request.getfixturevalue("setup")
    outbox(setup)
    setup.plans.planner = large_scan
    return _step_refusal(setup, "run_scan")


def step_plan_not_run(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """Runbook step check-outbox in Production, its plan unreadable: acknowledge it (run_noplan)."""
    setup: Setup = request.getfixturevalue("setup")
    outbox(setup)
    setup.plans.unbound = frozenset({"wallet_db"})
    return _step_refusal(setup, "run_noplan")


Case = Callable[[Path, pytest.FixtureRequest], Any]
CASES: dict[str, Case] = {
    "save-checks-environment-unbound": save_checks_environment_unbound,
    "save-checks-passing": save_checks_passing,
    "save-checks-time-filter": save_checks_time_filter,
    "draft-run-time-filter": draft_run_time_filter,
    "entity-save-plan-scan": entity_save_plan_scan,
    "entity-save-time-filter": entity_save_time_filter,
    "step-plan-scan": step_plan_scan,
    "step-plan-not-run": step_plan_not_run,
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_mocks_answer_as_the_backend_does(
    case: str, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    answer = CASES[case](tmp_path, request)
    path = ANSWERS / f"{case}.json"
    if os.environ.get("PROBEOWL_WRITE_MOCK_ANSWERS") == "1":
        path.write_text(json.dumps(answer, indent=2) + "\n", encoding="utf-8", newline="\n")
    assert path.exists(), f"{path} is missing: write it with PROBEOWL_WRITE_MOCK_ANSWERS=1"
    assert json.loads(path.read_text(encoding="utf-8")) == answer


def test_every_answer_file_has_a_case() -> None:
    # A file no case writes would let a mock drift with nothing checking it.
    assert sorted(p.stem for p in ANSWERS.glob("*.json")) == sorted(CASES)
