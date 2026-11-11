# Author: @SaadZarook
from typing import Any

import pytest
from pydantic import ValidationError

from probeowl.model import Cardinality, Entity


def visitor() -> dict[str, Any]:
    return {
        "entity": "visitor",
        "root_key": "visitor_profile_id",
        "identifiers": {
            "card_id": {"pattern": r"^C\d{6}$", "via": "cards.card_id", "unique": True},
            "email": {"via": "profile.email", "unique": False, "pii": True},
        },
        "locations": {
            "profile": {
                "using": "access_db",
                "lookup": "SELECT id AS visitor_profile_id, email FROM visitor_profile "
                "WHERE id = :visitor_profile_id",
                "exposes": ["visitor_profile_id", "email"],
                "pii": ["email"],
            },
            "cards": {
                "using": "access_db",
                "lookup": "SELECT card_id, visitor_profile_id, status FROM cards "
                "WHERE visitor_profile_id = :visitor_profile_id",
                "cardinality": "many",
                "max_rows": 50,
                "exposes": ["card_id", "visitor_profile_id", "status"],
                "each": {
                    "controller": {
                        "using": "door_controller_api",
                        "lookup": "GET /cards/{{card_id}}",
                        "max_fanout": 10,
                    }
                },
            },
        },
    }


def topup() -> dict[str, Any]:
    return {
        "entity": "topup",
        "root_key": "topup_id",
        "locations": {
            "record": {
                "using": "wallet_db",
                "lookup": "SELECT * FROM topups WHERE id = :topup_id",
                "exposes": ["user_id", "provider_payment_id", "ledger_txn_id", "created_at"],
            },
            "ledger": {
                "using": "ledger_db",
                "key_from": "record.ledger_txn_id",
                "lookup": "SELECT account FROM ledger_entries WHERE txn_id = :ledger_txn_id",
                "cardinality": "many",
            },
        },
    }


def test_visitor_entity_loads() -> None:
    entity = Entity.model_validate(visitor())
    assert entity.locations["cards"].cardinality is Cardinality.MANY
    assert entity.locations["cards"].each["controller"].max_fanout == 10


def test_root_key_is_added_as_a_unique_identifier() -> None:
    entity = Entity.model_validate(topup())
    assert entity.identifiers["topup_id"].unique
    assert entity.identifiers["topup_id"].via is None


def test_key_from_must_name_an_exposed_field() -> None:
    data = topup()
    data["locations"]["ledger"]["key_from"] = "record.ledger_id"
    with pytest.raises(ValidationError, match="location record does not expose ledger_id"):
        Entity.model_validate(data)


def test_key_from_must_name_a_known_location() -> None:
    data = topup()
    data["locations"]["ledger"]["key_from"] = "payment.ledger_txn_id"
    with pytest.raises(ValidationError, match="no location named payment"):
        Entity.model_validate(data)


def test_key_from_cycle_is_rejected() -> None:
    data = topup()
    data["locations"]["record"]["key_from"] = "ledger.account"
    data["locations"]["ledger"]["exposes"] = ["account"]
    with pytest.raises(ValidationError, match="key_from cycle"):
        Entity.model_validate(data)


def test_via_location_must_expose_the_root_key() -> None:
    data = visitor()
    data["locations"]["profile"]["exposes"] = ["email"]
    with pytest.raises(ValidationError, match="location profile must expose visitor_profile_id"):
        Entity.model_validate(data)


def test_via_location_must_expose_the_field() -> None:
    data = visitor()
    data["identifiers"]["employee_no"] = {"via": "profile.employee_no", "unique": False}
    with pytest.raises(ValidationError, match="location profile must expose employee_no"):
        Entity.model_validate(data)


def test_identifier_other_than_root_key_needs_via() -> None:
    data = visitor()
    data["identifiers"]["email"].pop("via")
    with pytest.raises(ValidationError, match="identifier email needs via"):
        Entity.model_validate(data)


def test_root_key_cannot_be_looked_up_via_a_location() -> None:
    data = visitor()
    data["identifiers"]["visitor_profile_id"] = {"via": "profile.email", "unique": True}
    with pytest.raises(ValidationError, match="root key visitor_profile_id cannot have via"):
        Entity.model_validate(data)


def test_root_key_must_be_unique() -> None:
    data = visitor()
    data["identifiers"]["visitor_profile_id"] = {"unique": False}
    with pytest.raises(ValidationError, match="must be unique"):
        Entity.model_validate(data)


def test_identifier_pattern_must_compile() -> None:
    data = visitor()
    data["identifiers"]["card_id"]["pattern"] = "^C(\\d{6}$"
    with pytest.raises(ValidationError, match="not a valid regular expression"):
        Entity.model_validate(data)


def test_fan_out_is_only_for_many_locations() -> None:
    data = visitor()
    data["locations"]["cards"]["cardinality"] = "one"
    data["locations"]["cards"].pop("max_rows")
    with pytest.raises(ValidationError, match="each applies only to cardinality many"):
        Entity.model_validate(data)


def test_max_rows_is_only_for_many_locations() -> None:
    data = topup()
    data["locations"]["record"]["max_rows"] = 10
    with pytest.raises(ValidationError, match="max_rows applies only to cardinality many"):
        Entity.model_validate(data)


def test_fan_out_needs_max_fanout() -> None:
    data = visitor()
    data["locations"]["cards"]["each"]["controller"].pop("max_fanout")
    with pytest.raises(ValidationError, match="max_fanout"):
        Entity.model_validate(data)


def test_fan_out_cannot_nest() -> None:
    data = visitor()
    data["locations"]["cards"]["each"]["controller"]["each"] = {}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Entity.model_validate(data)


def test_fan_out_name_cannot_reuse_a_location_name() -> None:
    data = visitor()
    data["locations"]["cards"]["each"] = {
        "profile": data["locations"]["cards"]["each"]["controller"]
    }
    with pytest.raises(ValidationError, match="reuses a location name"):
        Entity.model_validate(data)


def test_anchor_time_needs_a_window() -> None:
    data = topup()
    data["locations"]["ledger"]["anchor_time"] = "record.created_at"
    with pytest.raises(ValidationError, match="anchor_time needs a within window"):
        Entity.model_validate(data)


def test_anchor_time_must_name_an_exposed_field() -> None:
    data = topup()
    data["locations"]["ledger"]["within"] = "2m"
    data["locations"]["ledger"]["anchor_time"] = "record.posted_at"
    with pytest.raises(ValidationError, match="does not expose posted_at"):
        Entity.model_validate(data)


def test_unknown_key_is_rejected() -> None:
    data = topup()
    data["locations"]["record"]["cardinalty"] = "one"
    with pytest.raises(ValidationError, match="cardinalty"):
        Entity.model_validate(data)
