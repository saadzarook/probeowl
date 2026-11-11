# Author: @SaadZarook
from pathlib import Path

import pytest

from probeowl.errors import ConfigError
from probeowl.model import Probe
from probeowl.model.load import load_file, parse_document, read_text, read_yaml


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "doc.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_probe_is_not_mistaken_for_an_entity(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "probe: visitor_cards\nkind: inspect\nentity: visitor\nowner: access-team\n"
        "expect: count(cards) >= 1\n",
    )
    [document] = load_file(path)
    assert isinstance(document, Probe)


def test_several_documents_in_one_file(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "application: wallet\ntitle: Wallet\nowner: payments-team\n"
        "environments: {prod: {title: Production, production: true}}\n"
        "---\n"
        "probe: p\nkind: inspect\nentity: e\nowner: t\nexpect: count(x) >= 1\n",
    )
    assert len(load_file(path)) == 2


def test_unknown_document_kind_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match=r"x\.yaml: expected one of these top-level keys"):
        parse_document({"dashboard": "wallet"}, "x.yaml")


def test_validation_error_names_the_file_and_field() -> None:
    with pytest.raises(ConfigError, match=r"x\.yaml: invalid probe: owner: Field required"):
        parse_document({"probe": "p", "kind": "inspect", "entity": "e", "expect": "a"}, "x.yaml")


def test_invalid_yaml_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_file(write(tmp_path, "probe: [unclosed\n"))


def test_empty_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="the file is empty"):
        load_file(write(tmp_path, "# Author: @SaadZarook\n"))


def test_dates_stay_strings_as_in_the_json_schemas(tmp_path: Path) -> None:
    # PyYAML follows YAML 1.1 and would return a date; editors validating against
    # the JSON Schemas see a string, so the loader must too.
    [document] = read_yaml(write(tmp_path, "last_verified: 2026-10-12\n"))
    assert document == {"last_verified": "2026-10-12"}


# A probe whose `expect:` line was deleted: YAML folds the indented checks into owner (#260).
FOLDED = """probe: visitor_cards
title: A visitor's cards
kind: inspect
entity: visitor_profile
owner: access-team
  - count(cards where status == 'active') <= 1
"""


def test_checks_folded_into_another_field_say_a_line_is_missing() -> None:
    data = read_text(FOLDED)
    assert data["owner"].startswith("access-team - count(")  # what YAML made of it
    with pytest.raises(ConfigError) as err:
        parse_document(data, "the draft")
    message = str(err.value)
    assert message.startswith("the draft: this probe has no checks (expect:), and owner looks like")
    assert "is a line missing, such as expect: before the checks?" in message
    assert "Field required" not in message


def test_a_probe_with_no_checks_and_no_folded_text_keeps_the_usual_message() -> None:
    data = read_text(FOLDED.replace("  - count(cards where status == 'active') <= 1\n", ""))
    with pytest.raises(ConfigError, match="invalid probe: steps: Field required"):
        parse_document(data, "the draft")
