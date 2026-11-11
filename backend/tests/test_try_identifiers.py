# Author: @SaadZarook
"""With no identifier patterns, a value is tried as each identifier, root key first (#253).

The card access example as the entity wizard writes it: no patterns. A card ID must find its
visitor through cards.card_id, not be read as a visitor_profile_id and come back Missing.
"""

from dataclasses import replace
from typing import Any

import pytest

from probeowl.engine.identifiers import candidates
from probeowl.errors import RequestError
from probeowl.model.entity import Entity
from tests.card_fakes import VISITOR
from tests.conftest import Setup
from tests.test_runs import body, finished, start


def unpatterned(entity: Entity) -> Entity:
    identifiers = {n: i.model_copy(update={"pattern": None}) for n, i in entity.identifiers.items()}
    return entity.model_copy(update={"identifiers": identifiers})


@pytest.fixture
def no_patterns(setup: Setup) -> Setup:
    app = setup.applications["card_access"]
    visitor = unpatterned(app.entities["visitor"])
    setup.applications["card_access"] = replace(app, entities={**app.entities, "visitor": visitor})
    return setup


def run(setup: Setup, value: str) -> dict[str, Any]:
    client = setup.client()
    request = body(value, application="card_access", probe="visitor_cards", environment="uat")
    return finished(
        client, start(client, **{k: v for k, v in request.items() if k != "value"}, value=value)
    )


# --- Which identifiers are tried ------------------------------------------------------------


def test_with_no_patterns_every_identifier_is_tried_root_key_first() -> None:
    assert candidates(unpatterned(VISITOR), "C204518", None) == [
        "visitor_profile_id",
        "card_id",
        "employee_no",
        "email",
    ]


def test_a_chosen_identifier_or_a_matching_pattern_is_the_only_one() -> None:
    assert candidates(unpatterned(VISITOR), "C204518", "card_id") == ["card_id"]
    assert candidates(VISITOR, "C204518", None) == ["card_id"]


def test_identifiers_whose_pattern_does_not_match_are_not_tried() -> None:
    # The example's patterns, but employee_no has none: only it could hold an employee number.
    assert candidates(VISITOR, "EMP-100231", None) == ["employee_no"]


def test_nothing_that_could_hold_the_value_is_refused_before_any_lookup() -> None:
    patterned = VISITOR.model_copy(
        update={
            "identifiers": {n: i for n, i in VISITOR.identifiers.items() if i.pattern is not None}
        }
    )
    with pytest.raises(RequestError, match="does not look like any identifier of visitor"):
        candidates(patterned, "nothing-like-it", None)


# --- Runs ---------------------------------------------------------------------------------


def test_a_card_id_finds_its_visitor_with_no_patterns_and_says_how(no_patterns: Setup) -> None:
    found = run(no_patterns, "C204518")
    assert found["identifier"] == "card_id"
    assert found["subject"] == "VP00012931"
    assert found["resolution"]["outcome"] == "found"
    assert found["resolution"]["location"] == "cards"
    assert any("so each was tried" in w and "it is a card_id" in w for w in found["warnings"])
    # The probe then ran on the visitor: the planted case fails, it is not Missing.
    assert found["status"] == "failed"
    assert {r["location"]: r["outcome"] for r in found["locations"]}["profile"] == "found"


def test_a_root_key_with_no_patterns_is_tried_first_and_found(no_patterns: Setup) -> None:
    found = run(no_patterns, "VP00012931")
    assert found["identifier"] == "visitor_profile_id"
    assert found["subject"] == "VP00012931"
    assert any("it is a visitor_profile_id" in w for w in found["warnings"])


def test_a_value_no_identifier_finds_names_each_one_tried(no_patterns: Setup) -> None:
    found = run(no_patterns, "C000000")
    assert found["status"] == "failed"
    assert found["subject"] is None
    assert found["stop_reason"] == (
        "no visitor has this value as visitor_profile_id or card_id or employee_no or email"
    )


def test_a_value_finding_different_subjects_is_ambiguous(no_patterns: Setup) -> None:
    # Both seeded profiles share this employee number.
    found = run(no_patterns, "EMP-100231")
    assert found["status"] == "ambiguous"
    assert found["resolution"]["outcome"] == "ambiguous"
    assert sorted(m["subject"] for m in found["matches"]) == ["VP00008807", "VP00012931"]


def test_a_value_is_masked_until_an_identifier_that_is_not_pii_finds_it(
    no_patterns: Setup,
) -> None:
    # email is personal data and one of the identifiers tried: masked while it might be one.
    assert run(no_patterns, "C000000")["identifier_value"] == "[masked]"
    # Found as a card_id, which is not personal data: the result shows the value used.
    assert run(no_patterns, "C204518")["identifier_value"] == "C204518"
