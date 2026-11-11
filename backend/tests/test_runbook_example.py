# Author: @SaadZarook
"""The minimal runbook the Knowledge base's empty state offers is one owl serve loads.

tests/data/runbook_example.yaml is what the web app's runbookExample() writes for Card access
(web/e2e/runbook-example.spec.ts checks that); here it is added to the examples and loaded.
"""

import shutil
from pathlib import Path

from probeowl.model.check import check_folder, load_applications

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
EXAMPLE = Path(__file__).parent / "data" / "runbook_example.yaml"


def test_the_empty_states_example_runbook_loads_and_passes_owl_check(tmp_path: Path) -> None:
    folder = tmp_path / "config"
    shutil.copytree(EXAMPLES, folder)
    target = folder / "card-access" / "runbooks" / "visitor_profile_missing.yaml"
    target.parent.mkdir()
    shutil.copyfile(EXAMPLE, target)
    assert check_folder(folder).findings == []
    runbook = load_applications(folder)["card_access"].runbooks["visitor_profile_missing"]
    assert runbook.applies_to.when == ["profile.missing"]
    assert [step.kind for step in runbook.steps] == ["manual", "probe"]
