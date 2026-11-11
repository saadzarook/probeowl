# Author: @SaadZarook
from pathlib import Path

import pytest

from probeowl.model.schemas import CONTRACTS_DIR, build_schemas, write_schemas


@pytest.mark.parametrize("name", sorted(build_schemas()))
def test_contract_schema_matches_the_models(name: str) -> None:
    # The contract is the reviewed copy; a model change must come with a contract PR.
    path = CONTRACTS_DIR / name
    assert path.exists(), f"{path} is missing; run: uv run python -m probeowl.model.schemas"
    assert path.read_text(encoding="utf-8") == build_schemas()[name], (
        f"{path} is out of date; run: uv run python -m probeowl.model.schemas "
        "and open a contract PR"
    )


def test_write_schemas_writes_one_file_per_yaml_kind(tmp_path: Path) -> None:
    written = write_schemas(tmp_path)
    assert sorted(p.name for p in written) == [
        "application.schema.json",
        "catalog.schema.json",
        "connector.schema.json",
        "entity.schema.json",
        "probe.schema.json",
        "runbook.schema.json",
    ]
