# Author: @SaadZarook
"""Global search (#38): what it matches, what it never does, and nothing masked found or shown."""

from datetime import UTC, datetime
from typing import Any

import pytest

from probeowl.model.check import LoadedApplication
from probeowl.model.schema import ColumnSchema, SchemaSnapshot, TableSchema
from probeowl.search import search
from probeowl.store.schemas import save_snapshot
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.test_run_list import stored

EMAIL = "dana.khan@example.test"  # not a real person: a value to look for


def found(setup: Setup, q: str, **params: Any) -> dict[str, list[dict[str, Any]]]:
    response = setup.client().get("/api/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return {group["kind"]: group["items"] for group in response.json()["groups"]}


def ids(setup: Setup, q: str, kind: str, **params: Any) -> list[str]:
    return [item["id"] for item in found(setup, q, **params).get(kind, [])]


def imported(setup: Setup) -> None:
    """An import of wallet_db in UAT: the schema only, as the catalog's import saves it."""
    columns = [
        ColumnSchema(name=name, type=kind, nullable=False)
        for name, kind in [
            ("id", "varchar(20)"),
            ("status", "varchar(16)"),
            ("customer_email", "varchar(320)"),
            ("created_at", "datetime2"),
        ]
    ]
    snapshot = SchemaSnapshot(
        application="wallet",
        connector="wallet_db",
        environment="uat",
        imported_at=datetime(2026, 11, 1, tzinfo=UTC),
        tables=[
            TableSchema(table="topups", columns=columns, primary_key=["id"]),
            TableSchema(table="audit_trail", columns=columns[:1], primary_key=["id"]),
        ],
    )
    with setup.store.begin() as conn:
        save_snapshot(conn, LOCAL_WORKSPACE, snapshot)


# --- Each kind -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "kind", "expected"),
    [
        ("wallet", "application", "wallet"),
        ("top-up", "entity", "topup"),  # by its title
        ("topup_comp", "probe", "topup_completed"),
        ("ledger", "location", "topup.ledger"),
        ("checkout", "connector", "checkout_api"),
        ("not credited", "runbook", "topup_not_credited"),
        ("ledger missing", "runbook", "topup_not_credited"),  # by its symptom
    ],
)
def test_each_kind_in_the_configuration_is_found(
    setup: Setup, q: str, kind: str, expected: str
) -> None:
    assert expected in ids(setup, q, kind)


def test_a_probe_and_an_entity_say_what_they_are(setup: Setup) -> None:
    [probe] = [p for p in found(setup, "topup_completed")["probe"] if p["id"] == "topup_completed"]
    assert probe["detail"] == "Validate · topup"
    assert probe["application"] == "wallet"
    [entity] = found(setup, "topup", application="wallet")["entity"]
    assert entity["detail"] == "topup_id"


def test_imported_tables_and_columns_carry_their_labels_and_roles(setup: Setup) -> None:
    imported(setup)
    tables = {t["id"]: t for t in found(setup, "topups")["table"]}
    assert tables["wallet_db.topups"]["labels"] == ["transaction"]
    [audit] = found(setup, "audit_trail")["table"]
    assert audit["labels"][-1] == "suggested"  # not in catalog.yaml: its suggested label
    columns = {c["id"]: c for c in found(setup, "created_at")["column"]}
    created = columns["wallet_db.topups.created_at"]
    assert created["labels"] == ["time"]
    assert created["detail"] == "topups · datetime2"


def test_prefix_ranks_before_substring_and_names_before_titles(setup: Setup) -> None:
    probes = ids(setup, "topup", "probe")
    assert probes == sorted(probes)  # all prefixes: by name
    locations = ids(setup, "ledger", "location")
    assert locations[0] == "topup.ledger"  # the name, before a title that only mentions it


# --- Never matched ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "q",
    [
        "FROM topups WHERE",  # a location's lookup SQL
        "aggregate_id = :topup_id",  # a runbook step's SQL
        "Is the ledger posting stuck",  # a runbook step's title
        "127.0.0.1",  # a connector's host
        "http://127.0.0.1:8081",  # a REST connector's URL
        "probeowl/wallet_db.prod",  # a keychain name
        "keyring:",
        "PROBEOWL_WALLET_DB_UAT",  # an environment variable a secret names
        "WALLET-482",  # a run's investigation record
    ],
)
def test_text_search_must_never_look_at_is_not_matched(setup: Setup, q: str) -> None:
    stored(setup, "run-0001-aaaa", 0)
    assert found(setup, q) == {}


# --- Runs ------------------------------------------------------------------------------------


def test_a_run_is_found_by_a_prefix_of_its_id_from_four_characters(setup: Setup) -> None:
    stored(setup, "9f3c2a71-run", 0)
    assert ids(setup, "9f3c", "run") == ["9f3c2a71-run"]
    assert "run" not in found(setup, "9f3")


def test_a_run_is_found_by_its_subject_exactly_or_by_a_prefix_when_not_pii(setup: Setup) -> None:
    stored(setup, "older", 0)
    stored(setup, "newer", 5, subject="tp_91f2b9", identifier_value="tp_91f2b9")
    assert ids(setup, "TP_91F2C7", "run") == ["older"]  # exact, any case
    assert ids(setup, "tp_91f2", "run") == ["newer", "older"]  # a prefix, newest first
    [item] = found(setup, "tp_91f2c7")["run"]
    assert item["title"] == "tp_91f2c7"
    assert item["detail"] == "Failed · 01 Nov 09:00 · topup_completed"
    assert "run" not in found(setup, "f2c7")  # never by a substring


def test_a_masked_identifier_finds_no_run_and_the_response_never_holds_it(setup: Setup) -> None:
    # A visitor found by email: the email is PII, stored as [masked] with no subject resolved.
    stored(
        setup,
        "by-email",
        0,
        application="card_access",
        probe="visitor_cards",
        kind="inspect",
        entity="visitor",
        identifier="email",
        identifier_value="[masked]",
        subject=None,
    )
    for q in [EMAIL, "example.test", "dana.khan", "dana", "[masked]", "masked"]:
        response = setup.client().get("/api/search", params={"q": q})
        assert response.status_code == 200
        assert "run" not in {g["kind"] for g in response.json()["groups"]}, q
        # Only the query echoes it: no result holds it.
        assert EMAIL not in str(response.json()["groups"])


def pii_root(applications: dict[str, LoadedApplication]) -> dict[str, LoadedApplication]:
    """The wallet with topup's root key marked PII, as a team could configure it."""
    wallet = applications["wallet"]
    topup = wallet.entities["topup"]
    identifiers = dict(topup.identifiers)
    identifiers["topup_id"] = identifiers["topup_id"].model_copy(update={"pii": True})
    entities = dict(wallet.entities) | {
        "topup": topup.model_copy(update={"identifiers": identifiers})
    }
    changed = LoadedApplication(**{**wallet.__dict__, "entities": entities})
    return applications | {"wallet": changed}


def test_a_pii_subject_is_never_matched_whole_or_by_part_nor_shown(setup: Setup) -> None:
    stored(setup, "9f3c2a71-run", 0, identifier_value="[masked]")
    applications = pii_root(setup.applications)
    for q in ["tp_91f2c7", "tp_91f2", "tp_9"]:
        results = search(applications, setup.store, q)
        assert [g.kind for g in results.groups if g.kind == "run"] == [], q
    # Found by its id, it shows the subject masked.
    [group] = [g for g in search(applications, setup.store, "9f3c2a71").groups if g.kind == "run"]
    assert group.items[0].title == "[masked]"
    assert "tp_91f2c7" not in group.model_dump_json()


def test_a_run_of_an_entity_no_longer_loaded_is_treated_as_pii(setup: Setup) -> None:
    stored(setup, "gone-run", 0, entity="retired", identifier="retired_id")
    assert "run" not in found(setup, "tp_91f2c7")


# --- Scope, limits and errors ----------------------------------------------------------------


def test_application_scopes_the_search_and_omitted_searches_all(setup: Setup) -> None:
    assert ids(setup, "db", "connector", application="card_access") == ["access_db"]
    every = ids(setup, "db", "connector")
    assert {"access_db", "wallet_db", "ledger_db"} <= set(every)


def test_limit_is_per_group_and_more_counts_the_rest(setup: Setup) -> None:
    for minute in range(5):
        stored(setup, f"limit-run-{minute}", minute)
    response = setup.client().get("/api/search", params={"q": "tp_91f2c7", "limit": 2})
    [group] = [g for g in response.json()["groups"] if g["kind"] == "run"]
    assert [item["id"] for item in group["items"]] == ["limit-run-4", "limit-run-3"]
    assert group["more"] == 3


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"q": ""}, 422),
        ({"q": "   "}, 422),
        ({"q": "x" * 129}, 422),
        ({"q": "wallet", "limit": 0}, 422),
        ({"q": "wallet", "limit": 21}, 422),
        ({"q": "wallet", "application": "nowhere"}, 404),
    ],
)
def test_a_bad_query_is_a_422_and_an_unknown_application_a_404(
    setup: Setup, params: dict[str, Any], status: int
) -> None:
    response = setup.client().get("/api/search", params=params)
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")


def test_search_is_not_audited(setup: Setup) -> None:
    from probeowl.store.audit import list_events

    found(setup, "wallet")
    with setup.store.connect() as conn:
        assert list_events(conn, LOCAL_WORKSPACE) == []
