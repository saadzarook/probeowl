# Author: @SaadZarook
"""Identifier detection and the lookup that resolves an identifier to its subjects."""

from pathlib import Path
from typing import Any

import pytest

from probeowl.engine.identifiers import detect, resolution_lookup, resolve
from probeowl.errors import ConfigError, RequestError
from probeowl.guards.dialects import MARIADB, ORACLE, SQLSERVER
from probeowl.guards.statement import prepare
from probeowl.model import Entity
from probeowl.model.load import load_file
from probeowl.model.run import Outcome
from tests.wallet_fakes import Fake

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def visitor() -> Entity:
    [entity] = load_file(EXAMPLES / "card-access" / "entities" / "visitor.yaml")
    assert isinstance(entity, Entity)
    return entity


def entity_with(lookup: str, **identifier: Any) -> Entity:
    return Entity.model_validate(
        {
            "entity": "visitor",
            "root_key": "visitor_profile_id",
            "identifiers": {
                "email": {"via": "profile.email", "unique": False} | identifier,
            },
            "locations": {
                "profile": {
                    "using": "access_db",
                    "lookup": lookup,
                    "exposes": ["visitor_profile_id", "email"],
                }
            },
        }
    )


# --- Detection ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "identifier"),
    [
        ("VP00012931", "visitor_profile_id"),
        ("C204518", "card_id"),
        ("a.rahman@contractor.example.com", "email"),
    ],
)
def test_the_identifier_is_detected_from_its_pattern(value: str, identifier: str) -> None:
    assert detect(visitor(), value, None) == identifier


def test_a_value_no_pattern_matches_must_be_given_its_identifier() -> None:
    with pytest.raises(RequestError, match="does not look like any identifier of visitor") as err:
        detect(visitor(), "E-4471", None)
    assert "employee_no" in err.value.fields["identifier"]
    # Employee numbers have no pattern: chosen, they are accepted as entered.
    assert detect(visitor(), "E-4471", "employee_no") == "employee_no"


def test_a_value_several_patterns_match_must_be_given_its_identifier() -> None:
    entity = visitor()
    entity.identifiers["employee_no"] = entity.identifiers["employee_no"].model_copy(
        update={"pattern": r"^C\d+$"}
    )
    with pytest.raises(RequestError, match="looks like several identifiers: card_id, employee_no"):
        detect(entity, "C204518", None)
    assert detect(entity, "C204518", "card_id") == "card_id"


def test_a_chosen_identifier_must_match_its_pattern() -> None:
    with pytest.raises(RequestError, match="does not look like a card_id"):
        detect(visitor(), "VP00012931", "card_id")


def test_a_value_given_as_the_wrong_identifier_names_the_one_it_looks_like() -> None:
    # The retest's case: a card ID inspected as a visitor_profile_id.
    with pytest.raises(RequestError) as err:
        detect(visitor(), "C204518", "visitor_profile_id")
    assert str(err.value) == (
        "this value does not look like a visitor_profile_id; "
        "it looks like a card_id: choose that identifier"
    )
    assert err.value.fields["identifier"] == "use card_id"
    # Nothing it looks like: only the pattern.
    with pytest.raises(RequestError) as err:
        detect(visitor(), "nothing-like-it", "card_id")
    assert str(err.value) == "this value does not look like a card_id"
    assert "identifier" not in err.value.fields


# --- The resolution lookup ------------------------------------------------------------------


def test_the_via_locations_lookup_finds_rows_by_the_identifier() -> None:
    sql = resolution_lookup(visitor(), "card_id", MARIADB.sqlglot_dialect)
    assert "FROM cards WHERE card_id = :card_id" in sql
    assert "visitor_profile_id" in sql  # still selected: it gives the subject
    assert ":visitor_profile_id" not in sql


def test_an_aliased_column_is_matched_by_its_source_expression() -> None:
    entity = entity_with(
        "SELECT id AS visitor_profile_id, mail AS email FROM visitor_profile "
        "WHERE id = :visitor_profile_id"
    )
    sql = resolution_lookup(entity, "email", MARIADB.sqlglot_dialect)
    assert sql.endswith("WHERE mail = :email")


def test_other_conditions_are_kept() -> None:
    entity = entity_with(
        "SELECT v.id AS visitor_profile_id, v.email FROM visitor_profile AS v "
        "WHERE v.deleted = 0 AND (:visitor_profile_id = v.id)"
    )
    sql = resolution_lookup(entity, "email", MARIADB.sqlglot_dialect)
    assert "v.deleted = 0" in sql
    assert "v.email = :email" in sql


def test_select_star_matches_the_field_by_name() -> None:
    entity = entity_with(
        "SELECT * FROM visitor_profile WHERE visitor_profile_id = :visitor_profile_id"
    )
    assert resolution_lookup(entity, "email", MARIADB.sqlglot_dialect).endswith(
        "WHERE email = :email"
    )


@pytest.mark.parametrize("rules", [SQLSERVER, ORACLE, MARIADB], ids=lambda r: r.name)
def test_the_resolution_lookup_passes_the_guard_in_every_dialect(rules: Any) -> None:
    sql = resolution_lookup(visitor(), "card_id", rules.sqlglot_dialect)
    guarded = prepare(sql, {"card_id": "C204518"}, rules, 51)
    assert "C204518" not in guarded.sql  # bound, never interpolated


@pytest.mark.parametrize(
    ("lookup", "message"),
    [
        (
            "SELECT id AS visitor_profile_id, email FROM visitor_profile "
            "WHERE id = :visitor_profile_id OR email = 'x'",
            "exactly one condition <column> = :visitor_profile_id",
        ),
        (
            "SELECT id AS visitor_profile_id, email FROM visitor_profile",
            "exactly one condition",
        ),
        (
            "SELECT id AS visitor_profile_id, email FROM visitor_profile "
            "WHERE id = :visitor_profile_id AND site = :site",
            "may use only :visitor_profile_id",
        ),
        (
            "SELECT id AS visitor_profile_id FROM visitor_profile WHERE id = :visitor_profile_id",
            "does not select email",
        ),
        (
            "SELECT id AS visitor_profile_id, email FROM a WHERE id = :visitor_profile_id "
            "UNION SELECT id, email FROM b WHERE id = :visitor_profile_id",
            "must be a plain SELECT",
        ),
    ],
)
def test_a_lookup_that_cannot_be_rewritten_is_a_config_error(lookup: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        resolution_lookup(entity_with(lookup), "email", MARIADB.sqlglot_dialect)


def test_a_via_location_keyed_from_another_location_is_a_config_error() -> None:
    entity = Entity.model_validate(
        {
            "entity": "topup",
            "root_key": "topup_id",
            "identifiers": {"payment_ref": {"via": "payment.reference", "unique": True}},
            "locations": {
                "record": {
                    "using": "db",
                    "lookup": "SELECT id AS topup_id, pay_id FROM t WHERE id = :topup_id",
                    "exposes": ["topup_id", "pay_id"],
                },
                "payment": {
                    "using": "db",
                    "key_from": "record.pay_id",
                    "lookup": "SELECT reference, topup_id FROM p WHERE id = :pay_id",
                    "exposes": ["reference", "topup_id"],
                },
            },
        }
    )
    with pytest.raises(ConfigError, match="must be keyed by the root key"):
        resolution_lookup(entity, "payment_ref", MARIADB.sqlglot_dialect)


# --- Resolving --------------------------------------------------------------------------------


def test_rows_for_one_subject_are_one_match() -> None:
    rows = [
        {"visitor_profile_id": "VP00012931", "email": "a@x.example.com"},
        {"visitor_profile_id": "VP00012931", "email": "a@x.example.com"},
    ]
    entity = entity_with("SELECT * FROM visitor_profile WHERE id = :visitor_profile_id")
    found = resolve(entity, "email", "a@x.example.com", "SELECT 1", Fake(lambda q, p: rows))
    assert found.subjects == ["VP00012931"]
    assert found.result.outcome is Outcome.FOUND


def test_more_rows_than_are_read_say_so() -> None:
    rows = [
        {"visitor_profile_id": f"VP{i:08d}", "email": "shared@x.example.com"} for i in range(60)
    ]
    entity = entity_with("SELECT * FROM visitor_profile WHERE id = :visitor_profile_id")
    found = resolve(entity, "email", "shared@x.example.com", "SELECT 1", Fake(lambda q, p: rows))
    assert found.result.outcome is Outcome.AMBIGUOUS
    assert len(found.subjects) == 50
    assert found.result.reason == "email matches 50 subjects; more than 50 rows"
