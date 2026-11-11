# Author: @SaadZarook
"""The entity wizard's save, end to end on a real MariaDB: a visitor and their cards, as the
wizard writes them, saved through every save check (the plan check reads real plans), then a
probe on it, an Inspect run that finds the subject, and owl check on the folder.

The keychain is a stand-in (a dict); everything else is real.
"""

import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymysql
import pytest
from sqlalchemy.engine import make_url
from typer.testing import CliRunner

from probeowl import secrets
from probeowl.api.app import create_app
from probeowl.checks.saving import ProbeWrites
from probeowl.cli import connector_setup
from probeowl.cli import init as init_wizard
from probeowl.cli import main as cli
from probeowl.config_folder import ConfigFolder
from probeowl.model.check import check_folder, load_applications
from probeowl.runs import RunService
from probeowl.store.engine import create_store_engine
from tests.local_client import local_client
from tests.store_databases import store_database_url

pytestmark = pytest.mark.database


@pytest.fixture
def mariadb() -> Iterator[tuple[str, int, str, str, str, str]]:
    """The test database with its own visitor and cards tables.

    Yields host, port, database, user, password and the tables' suffix.
    """
    with store_database_url("mariadb") as url:
        parsed = make_url(url)
        host, port = parsed.host or "127.0.0.1", parsed.port or 3306
        user, password, database = parsed.username or "", parsed.password or "", parsed.database
        assert database is not None
        suffix = uuid.uuid4().hex[:8]
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password, database=database, autocommit=True
        )
        try:
            with conn.cursor() as cursor:
                # The test's own tables in the test database, made and dropped here.
                cursor.execute(
                    f"CREATE TABLE wz_profile_{suffix} (id VARCHAR(12) NOT NULL PRIMARY KEY, "
                    "status VARCHAR(10) NOT NULL, email VARCHAR(64) NOT NULL)"
                )
                cursor.execute(
                    f"CREATE TABLE wz_cards_{suffix} (card_id VARCHAR(12) NOT NULL PRIMARY KEY, "
                    "visitor_profile_id VARCHAR(12) NOT NULL, status VARCHAR(10) NOT NULL, "
                    f"INDEX ix_wz_cards_{suffix}_visitor (visitor_profile_id))"
                )
                cursor.execute(
                    f"INSERT INTO wz_profile_{suffix} "
                    "VALUES ('VP00000001', 'active', 'a@b.example')"
                )
                cursor.execute(
                    f"INSERT INTO wz_cards_{suffix} VALUES ('C000001', 'VP00000001', 'active'), "
                    "('C000002', 'VP00000001', 'revoked')"
                )
            yield host, port, database, user, password, suffix
        finally:
            with conn.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS wz_cards_{suffix}")
                cursor.execute(f"DROP TABLE IF EXISTS wz_profile_{suffix}")
            conn.close()


def wizard_yaml(suffix: str) -> str:
    """What the wizard writes for visitor_profile, then cards by the catalog's link."""
    return f"""# Written by the entity wizard. Edit freely; owl check <folder> checks it.
entity: badge_holder
title: Badge holder
root_key: badge_holder_id
owner: access-team
identifiers:
  badge_holder_id:
    unique: true
  card_id:
    via: cards.card_id
    unique: true
locations:
  profile:
    title: Wz profile
    using: access_db
    lookup: SELECT id AS badge_holder_id, status, email FROM wz_profile_{suffix} WHERE id = :badge_holder_id
    exposes:
      - badge_holder_id
      - status
      - email
    pii:
      - email
  cards:
    title: Wz cards
    using: access_db
    lookup: SELECT card_id, visitor_profile_id AS badge_holder_id, status FROM wz_cards_{suffix} WHERE visitor_profile_id = :badge_holder_id
    cardinality: many
    max_rows: 100
    exposes:
      - card_id
      - badge_holder_id
      - status
"""  # noqa: E501 - the wizard writes each lookup on one line


PROBE = """probe: badge_cards
kind: inspect
entity: badge_holder
owner: access-team
expect:
  - count(cards) >= 1
  - count(cards where status == 'active') <= 1
"""


def finished(api: Any, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run: dict[str, Any] = api.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} still running")


def test_a_wizard_entity_is_checked_on_real_plans_saved_and_inspected(
    mariadb: tuple[str, int, str, str, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, port, database, user, password, suffix = mariadb
    keychain: dict[str, str] = {}
    monkeypatch.setattr(init_wizard, "check_keychain", lambda: None)
    monkeypatch.setattr(
        connector_setup, "save_secret", lambda s, u, v: keychain.__setitem__(f"{s}/{u}", v)
    )
    monkeypatch.setattr(secrets, "_from_keyring", lambda target: keychain[target])
    store = f"sqlite:///{tmp_path / 'store.db'}"
    monkeypatch.setenv("PROBEOWL_STORE_URL", store)
    folder = tmp_path / "probeowl"
    answers = [
        str(folder), "access", "", "access-team", "uat", "none", "",
        "mariadb", "access_db", host, str(port), database, user, password,
        "n", "y", "y", "self-signed test certificate",
        "y", "n", "n",  # create the store; no connection test; no import
    ]  # fmt: skip
    assert CliRunner().invoke(cli.app, ["init"], input="\n".join(answers) + "\n").exit_code == 0

    # owl serve, as it is built for this folder, with the plan check on the real database.
    applications = load_applications(folder)
    engine = create_store_engine(store)
    runs = RunService(applications, engine)
    writes = ProbeWrites(applications, engine, ConfigFolder(folder))
    api = local_client(create_app(runs, probes=writes))

    url = "/api/applications/access/entities/badge_holder"
    body = {"entity": wizard_yaml(suffix), "environment": "uat"}
    preview = api.put(url, json=body)
    assert preview.status_code == 200, preview.text
    report = preview.json()["report"]
    assert report["can_save"] is True, report
    plans = [c for c in report["checks"] if "plan" in c["check"].lower()]
    assert plans and not any(c["plan_not_run"] for c in plans), report["checks"]
    assert api.put(url, json=body | {"base": preview.json()["base"]}).json()["written"] is True

    probe_url = "/api/applications/access/probes/badge_cards"
    probe = {"probe": PROBE, "environment": "uat"}
    base = api.put(probe_url, json=probe).json()["base"]
    assert api.put(probe_url, json=probe | {"base": base}).json()["written"] is True
    assert check_folder(folder).findings == []

    started = api.post(
        "/api/runs",
        json={
            "application": "access",
            "environment": "uat",
            "probe": "badge_cards",
            # A card ID, found through the card identifier, then the subject by its root key.
            "value": "C000002",
            "identifier": "card_id",
            "investigation": {"reason": "proactive_check"},
        },
    )
    assert started.status_code == 202, started.text
    run = finished(api, started.json()["id"])
    why = [(r["location"], r["outcome"], r.get("reason")) for r in run["locations"]]
    why += [(e["expectation"], e["result"], e.get("detail")) for e in run.get("expectations") or []]
    assert run["status"] == "passed", why
    assert run["subject"] == "VP00000001"
    rows = {r["location"]: (r["evidence"] or {}).get("rows") or [] for r in run["locations"]}
    assert {r["location"]: r["outcome"] for r in run["locations"]} == {
        "profile": "found",
        "cards": "found",
    }
    assert len(rows["cards"]) == 2
    assert rows["profile"][0]["email"] == "[masked]"  # the wizard listed it in pii
    runs.shutdown()
    engine.dispose()
