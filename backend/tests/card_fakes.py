# Author: @SaadZarook
"""Fake connectors serving the planted card access cases (dev/seed/PLANTED.md) for tests."""

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from probeowl.model import Entity, Probe
from probeowl.model.load import load_file
from tests.wallet_fakes import Fake

CARD_ACCESS = Path(__file__).resolve().parents[2] / "examples" / "card-access"

# dev/seed/mariadb/access.sql and dev/fakes/door_controller.py. Dates are relative to today, so
# C204518 stays valid and C198220 expired whenever the tests run (the checks use now()).
TODAY = date.today()
PROFILES: dict[str, dict[str, Any]] = {
    "VP00012931": {"email": "contractor.one@example.com", "status": "active"},
    "VP00008807": {"email": "contractor.one@example.com", "status": "inactive"},
}
CARDS: list[dict[str, Any]] = [
    {
        "card_id": "C204518",
        "visitor_profile_id": "VP00012931",
        "status": "active",
        "valid_from": TODAY - timedelta(days=60),
        "valid_to": TODAY + timedelta(days=60),
        "access_group": "HQ-LOBBY",
    },
    {
        "card_id": "C198220",
        "visitor_profile_id": "VP00012931",
        "status": "active",
        "valid_from": TODAY - timedelta(days=300),
        "valid_to": TODAY - timedelta(days=30),
        "access_group": "HQ-LOBBY",
    },
    {
        "card_id": "C177031",
        "visitor_profile_id": "VP00012931",
        "status": "revoked",
        "valid_from": TODAY - timedelta(days=300),
        "valid_to": TODAY + timedelta(days=60),
        "access_group": "HQ-PARKING",
    },
]
# The controller still reports C177031 as active: the planted mismatch.
CONTROLLER = {"C204518": "active", "C198220": "active", "C177031": "active"}


def load(name: str) -> Any:
    [document] = load_file(CARD_ACCESS / name)
    return document


VISITOR: Entity = load("entities/visitor.yaml")
VISITOR_CARDS: Probe = load("probes/visitor_cards.yaml")


def access_db(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
    if "FROM cards" in lookup:
        if "card_id" in p and "visitor_profile_id" not in p:
            return [dict(c) for c in CARDS if c["card_id"] == p["card_id"]]
        return [dict(c) for c in CARDS if c["visitor_profile_id"] == p["visitor_profile_id"]]
    return [
        {
            "visitor_profile_id": profile_id,
            "employee_no": "EMP-100231",
            "email": profile["email"],
            "type": "contractor",
            "status": profile["status"],
            "created_at": datetime(2025, 11, 3, 9, 0),
        }
        for profile_id, profile in PROFILES.items()
        if p.get("visitor_profile_id") == profile_id
        or p.get("email") == profile["email"]
        or p.get("employee_no") == "EMP-100231"
    ]


def door_controller(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
    status = CONTROLLER.get(str(p["card_id"]))
    return [] if status is None else [{"card_id": p["card_id"], "status": status}]


def card_connectors(**changes: Fake) -> dict[str, Fake]:
    """Fresh fakes for every card access connector, with any replaced."""
    return {"access_db": Fake(access_db), "door_controller_api": Fake(door_controller)} | changes
