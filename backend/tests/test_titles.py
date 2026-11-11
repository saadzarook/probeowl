# Author: @SaadZarook
"""Display titles: from the YAML, else the name with spaces; set on a run only when it is read."""

from typing import Any

import pytest

from probeowl.model import Entity, Probe
from probeowl.model.common import humanized
from probeowl.store.runs import get_run
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.test_runs import finished, start

LOCATION: dict[str, Any] = {"using": "db", "lookup": "SELECT 1"}


def entity(**changes: Any) -> Entity:
    data: dict[str, Any] = {
        "entity": "visitor_card",
        "root_key": "card_id",
        "locations": {
            "card_record": LOCATION,
            "cards": {
                **LOCATION,
                "cardinality": "many",
                "each": {"door_controller": {**LOCATION, "max_fanout": 5}},
            },
        },
    }
    return Entity.model_validate(data | changes)


@pytest.mark.parametrize(
    ("name", "shown"),
    [("visitor_cards", "Visitor cards"), ("ledger", "Ledger"), ("tp_2", "Tp 2")],
)
def test_a_name_without_a_title_is_shown_with_spaces(name: str, shown: str) -> None:
    assert humanized(name) == shown


def test_an_entity_and_its_locations_without_titles_get_their_names_spaced() -> None:
    found = entity()
    assert found.title == "Visitor card"
    assert found.locations["card_record"].title == "Card record"
    assert found.locations["cards"].each["door_controller"].title == "Door controller"


def test_a_title_in_the_yaml_is_kept() -> None:
    locations = entity().model_dump()["locations"]
    locations["cards"]["each"]["door_controller"]["title"] = "Door controller API"
    found = entity(title="Visitor", locations=locations)
    assert found.title == "Visitor"
    assert found.locations["cards"].each["door_controller"].title == "Door controller API"


def test_a_probe_without_a_title_gets_its_name_spaced() -> None:
    data = {"probe": "visitor_cards", "kind": "inspect", "entity": "v", "owner": "t"}
    assert Probe.model_validate({**data, "expect": ["exists(cards)"]}).title == "Visitor cards"
    titled = Probe.model_validate({**data, "title": "Cards", "expect": ["exists(cards)"]})
    assert titled.title == "Cards"


# --- Through the API --------------------------------------------------------------------------


def test_the_probe_list_and_entities_carry_titles(setup: Setup) -> None:
    client = setup.client()
    [probe] = client.get("/api/applications/wallet/probes").json()
    assert (probe["name"], probe["title"]) == ("topup_completed", "Top-up completed")
    topup = client.get("/api/applications/wallet/entities/topup").json()
    assert topup["title"] == "Top-up"
    assert topup["locations"]["ledger"]["title"] == "Ledger entries"
    visitor = client.get("/api/applications/card_access/entities/visitor").json()
    assert visitor["locations"]["cards"]["each"]["controller"]["title"] == "Door controller"


def test_a_run_is_read_with_its_probe_and_location_titles(setup: Setup) -> None:
    client = setup.client()
    run = finished(client, start(client, value="tp_91f2c7"))
    assert run["probe_title"] == "Top-up completed"
    titles = {r["location"]: r["title"] for r in run["locations"]}
    assert titles == {
        "record": "Top-up record",
        "payment": "Provider payment",
        "webhook": "Webhook deliveries",
        "ledger": "Ledger entries",
    }


def test_a_fan_out_lookup_and_the_identifier_lookup_are_titled_too(setup: Setup) -> None:
    client = setup.client()
    run = finished(
        client,
        start(client, application="card_access", probe="visitor_cards", value="C204518"),
    )
    assert run["resolution"]["title"] == "Cards"
    controllers = {r["title"] for r in run["locations"] if r["location"] == "controller"}
    assert controllers == {"Door controller"}


def test_titles_are_never_stored(setup: Setup) -> None:
    client = setup.client()
    run_id = start(client, value="tp_91f2c7")
    finished(client, run_id)
    with setup.store.connect() as conn:
        stored = get_run(conn, LOCAL_WORKSPACE, run_id)
    assert stored.probe_title == ""
    assert {location.title for location in stored.locations} == {""}


def test_a_run_whose_probe_was_removed_since_still_reads(setup: Setup) -> None:
    client = setup.client()
    run_id = start(client, value="tp_91f2c7")
    finished(client, run_id)
    del setup.applications["wallet"].probes["topup_completed"]
    del setup.applications["wallet"].entities["topup"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["probe_title"] == "Topup completed"
    assert outcome_titles(run) >= {"Ledger"}


def outcome_titles(run: dict[str, Any]) -> set[str]:
    return {r["title"] for r in run["locations"]}
