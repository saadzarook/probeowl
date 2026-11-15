# Author: @SaadZarook
"""The web's mocks say exactly what the backend says (#108).

Each file in web/mocks/fixtures/backend/ is the backend's answer to one named case, which the mocks
import as it is. Each case is built here for real, and the test fails if the answer differs from
the file in message or shape. After a deliberate change, write the files again with
PROBEOWL_WRITE_MOCK_ANSWERS=1 and review the diff.
"""

import dataclasses
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from probeowl.checks.service import check_draft
from probeowl.model.check import LoadedApplication, load_applications

REPOSITORY = Path(__file__).resolve().parents[2]
EXAMPLES = REPOSITORY / "examples"
ANSWERS = REPOSITORY / "web" / "mocks" / "fixtures" / "backend"


def _unbound(app: LoadedApplication, connector: str, environment: str) -> LoadedApplication:
    found = app.connectors[connector]
    kept = {k: v for k, v in found.environments.items() if k != environment}
    unbound = found.model_copy(update={"environments": kept})
    return dataclasses.replace(app, connectors={**app.connectors, connector: unbound})


def save_checks_environment_unbound() -> Any:
    """visitor_cards in Card access, access_db unbound in Production (mock-prod-unbound)."""
    app = _unbound(load_applications(EXAMPLES)["card_access"], "access_db", "prod")
    probe = (EXAMPLES / "card-access" / "probes" / "visitor_cards.yaml").read_text(encoding="utf-8")
    return check_draft(app, probe).model_dump(mode="json")


CASES: dict[str, Callable[[], Any]] = {
    "save-checks-environment-unbound": save_checks_environment_unbound,
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_mocks_answer_as_the_backend_does(case: str) -> None:
    answer = CASES[case]()
    path = ANSWERS / f"{case}.json"
    if os.environ.get("PROBEOWL_WRITE_MOCK_ANSWERS") == "1":
        path.write_text(json.dumps(answer, indent=2) + "\n", encoding="utf-8", newline="\n")
    assert path.exists(), f"{path} is missing: write it with PROBEOWL_WRITE_MOCK_ANSWERS=1"
    assert json.loads(path.read_text(encoding="utf-8")) == answer


def test_every_answer_file_has_a_case() -> None:
    # A file no case writes would let a mock drift with nothing checking it.
    assert sorted(p.stem for p in ANSWERS.glob("*.json")) == sorted(CASES)
