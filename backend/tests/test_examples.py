# Author: @SaadZarook
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from probeowl.model import Entity
from probeowl.model.load import document_kind, load_file, read_yaml
from probeowl.model.schemas import CONTRACTS_DIR

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
EXAMPLE_FILES = sorted(EXAMPLES_DIR.rglob("*.yaml"))


def test_examples_exist() -> None:
    assert {p.parent.name for p in EXAMPLES_DIR.glob("*/application.yaml")} == {
        "wallet",
        "card-access",
    }


@pytest.mark.parametrize(
    "path", EXAMPLE_FILES, ids=lambda p: p.relative_to(EXAMPLES_DIR).as_posix()
)
def test_example_loads_into_the_models(path: Path) -> None:
    load_file(path)


@pytest.mark.parametrize(
    "path", EXAMPLE_FILES, ids=lambda p: p.relative_to(EXAMPLES_DIR).as_posix()
)
def test_example_validates_against_the_contract_schema(path: Path) -> None:
    for document in read_yaml(path):
        kind = document_kind(document, str(path))
        schema = json.loads((CONTRACTS_DIR / f"{kind}.schema.json").read_text(encoding="utf-8"))
        errors = [e.message for e in Draft202012Validator(schema).iter_errors(document)]
        assert errors == []


def entity(app: str, name: str) -> Entity:
    [document] = load_file(EXAMPLES_DIR / app / "entities" / f"{name}.yaml")
    assert isinstance(document, Entity)
    return document


# The planted IDs in dev/seed/PLANTED.md must be recognised by the identifier patterns.
@pytest.mark.parametrize(
    ("app", "name", "identifier", "value"),
    [
        ("wallet", "topup", "topup_id", "tp_91f2c7"),
        ("wallet", "topup", "topup_id", "tp_93b001"),
        ("wallet", "topup", "provider_payment_id", "pay_7hq2m9"),
        ("wallet", "topup", "ledger_txn_id", "lt_55e1a0"),
        ("card-access", "visitor", "visitor_profile_id", "VP00012931"),
        ("card-access", "visitor", "visitor_profile_id", "VP00008807"),
        ("card-access", "visitor", "card_id", "C204518"),
        ("card-access", "visitor", "card_id", "C177031"),
    ],
)
def test_planted_ids_match_their_identifier(
    app: str, name: str, identifier: str, value: str
) -> None:
    matches = [
        other
        for other, ident in entity(app, name).identifiers.items()
        if ident.pattern and re.fullmatch(ident.pattern, value)
    ]
    assert matches == [identifier]
