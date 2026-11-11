# Author: @SaadZarook
"""GET /api/me: the person running owl serve, as runs and the audit name them (#255)."""

from probeowl.api.app import create_app
from tests.conftest import Setup
from tests.local_client import local_client
from tests.test_runs import finished, start


def test_me_is_the_actor_runs_are_recorded_under(setup: Setup) -> None:
    client = setup.client()
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json() == {"kind": "person", "name": "s.khan", "on_behalf_of": None}
    run = finished(client, start(client))
    assert run["actor"]["name"] == me.json()["name"]


def test_without_runs_it_says_so() -> None:
    response = local_client(create_app()).get("/api/me")
    assert response.status_code == 501
    assert response.headers["content-type"].startswith("application/problem+json")
