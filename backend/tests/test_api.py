# Author: @SaadZarook
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from probeowl.api.app import create_app
from probeowl.api.models import RunbookView
from probeowl.api.openapi import CONTRACT_PATH, build_openapi
from probeowl.errors import ConfigError, ConnectorError, GuardViolation, NotFoundError
from probeowl.model import Runbook
from tests.local_client import local_client

PROBLEM_JSON = "application/problem+json"


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with local_client(app) as test_client:
        yield test_client


def start_run_body() -> dict[str, object]:
    return {
        "application": "wallet",
        "environment": "prod",
        "probe": "topup_completed",
        "value": "tp_91f2c7",
        "investigation": {"reason": "incident", "reference": "WALLET-482"},
    }


def test_contract_matches_the_api() -> None:
    # The contract is the reviewed copy; a route change must come with a contract PR.
    assert CONTRACT_PATH.exists(), "run: uv run python -m probeowl.api.openapi"
    assert CONTRACT_PATH.read_text(encoding="utf-8") == build_openapi(), (
        f"{CONTRACT_PATH} is out of date; run: uv run python -m probeowl.api.openapi "
        "and open a contract PR"
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/applications"),
        ("get", "/api/applications/wallet"),
        ("get", "/api/applications/wallet/environments"),
        ("get", "/api/applications/wallet/entities"),
        ("get", "/api/applications/wallet/entities/topup"),
        ("get", "/api/applications/wallet/probes"),
        ("get", "/api/runs/8812"),
        ("get", "/api/applications/wallet/catalog"),
        ("get", "/api/applications/wallet/runbooks"),
        ("get", "/api/applications/wallet/runbooks/topup_not_credited"),
    ],
)
def test_stub_routes_answer_501_as_problem_details(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path)
    assert response.status_code == 501
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Implemented",
        "status": 501,
        "detail": response.json()["detail"],
        "instance": path,
    }


def test_start_run_is_async_in_the_contract(app: FastAPI) -> None:
    responses = app.openapi()["paths"]["/api/runs"]["post"]["responses"]
    assert "Location" in responses["202"]["headers"]
    assert responses["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RunStarted"
    }
    assert "200" not in responses
    assert "201" not in responses


def test_no_route_can_execute_a_runbook_step(app: FastAPI) -> None:
    # One route runs a step. It takes a step's ID, never SQL or values, and the server decides:
    # only a step it marks runnable runs, and a write query is always refused. See test_run_steps.
    run_step = app.openapi()["paths"]["/api/runs/{run_id}/runbooks/{runbook}/steps/{step}/run"]
    parameters = {p["name"] for p in run_step["post"]["parameters"]}
    assert parameters == {"run_id", "runbook", "step"}
    schemas = app.openapi()["components"]["schemas"]
    assert set(schemas["RunRunbookStep"]["properties"]) == {"run_without_plan_check"}
    assert set(schemas["TickRunbookStep"]["properties"]) == {"done"}
    # Everything else that is not a GET starts probe runs or writes config, and takes no SQL.
    writes = [
        (path, method)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method != "get"
    ]
    assert sorted(writes) == [
        ("/api/applications/{application}/catalog/imports", "post"),  # reads schema, never data
        # Writes catalog.yaml in the loaded folder: config, never a probed database.
        ("/api/applications/{application}/catalog/tables/{connector}/{table}", "put"),
        # Connects once to a binding, or GETs a REST API's base URL once; reads no data.
        ("/api/applications/{application}/connectors/{connector}/test", "post"),
        # Writes a new entity's YAML in the loaded folder: config, never a probed database.
        ("/api/applications/{application}/entities/{entity}", "put"),
        # Checks a draft; reads plans at most, never data.
        ("/api/applications/{application}/probes/checks", "post"),
        # Starts a run of a draft probe, like POST /api/runs.
        ("/api/applications/{application}/probes/test-runs", "post"),
        # Writes the probe's YAML in the loaded folder: config, never a probed database.
        ("/api/applications/{application}/probes/{probe}", "put"),
        # Records that a write query was copied, and returns its text. Never runs it.
        ("/api/applications/{application}/runbooks/{runbook}/steps/{step}/copy", "post"),
        # Reads the loaded config folder again: takes no body, reads no probed database.
        ("/api/config/reload", "post"),
        # Turn a kill switch on and off: stored in ProbeOwl's own store, and they stop reads.
        ("/api/kill-switches", "post"),
        ("/api/kill-switches/release", "post"),
        ("/api/runs", "post"),
        # Runs the probe on known-good candidates, like POST /api/runs.
        ("/api/runs/{run_id}/healthy-comparison", "post"),
        ("/api/runs/{run_id}/matches", "post"),
        # Records that a write query was copied, and returns its text. Never runs it.
        ("/api/runs/{run_id}/runbooks/{runbook}/steps/{step}/copy", "post"),
        # Runs one step the server marks runnable. Takes a step's ID, never SQL.
        ("/api/runs/{run_id}/runbooks/{runbook}/steps/{step}/run", "post"),
        # Saves a manual step's tick with the run. Reads nothing.
        ("/api/runs/{run_id}/runbooks/{runbook}/steps/{step}/tick", "post"),
    ]
    assert "requestBody" not in app.openapi()["paths"]["/api/config/reload"]["post"]
    assert set(app.openapi()["components"]["schemas"]["PickMatches"]["properties"]) == {"subjects"}
    assert set(app.openapi()["components"]["schemas"]["StartRun"]["properties"]) == {
        "application",
        "environment",
        "probe",
        "value",
        "identifier",
        "investigation",
    }


def runbook_view(write_runnable: bool) -> dict[str, object]:
    return {
        "runbook": "topup_not_credited",
        "title": "Top-up captured but wallet not credited",
        "application": "wallet",
        "applies_to": {"entity": "topup", "when": ["ledger.missing"]},
        "owner": "payments-team",
        "last_verified": "2026-10-12",
        "steps": [
            {
                "kind": "write_query",
                "title": "Re-queue the ledger posting",
                "using": "wallet_db",
                "requires": "change approval",
                "sql": "UPDATE outbox_events SET status = 'pending'",
                "id": "requeue-posting",
                "runnable": write_runnable,
            }
        ],
    }


def test_write_query_step_is_never_runnable() -> None:
    RunbookView.model_validate(runbook_view(write_runnable=False))
    with pytest.raises(ValidationError, match="Input should be False"):
        RunbookView.model_validate(runbook_view(write_runnable=True))


def test_runnable_is_not_accepted_in_runbook_yaml() -> None:
    data = runbook_view(write_runnable=False)
    data["steps"][0].pop("runnable")  # type: ignore[index]
    data["steps"][0]["runnable"] = True  # type: ignore[index]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Runbook.model_validate(data)


def test_start_run_stub_validates_then_answers_501(client: TestClient) -> None:
    response = client.post("/api/runs", json=start_run_body())
    assert response.status_code == 501


def test_invalid_request_is_a_422_problem(client: TestClient) -> None:
    body = start_run_body()
    body["investigation"] = {"reason": "curiosity"}
    response = client.post("/api/runs", json=body)
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert "investigation.reason" in response.json()["detail"]


def test_unknown_path_is_a_404_problem(client: TestClient) -> None:
    response = client.get("/api/nothing-here")
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotFoundError("no probe named topup_done"), 404),
        (ConfigError("wallet_db has no binding for prod"), 422),
        (GuardViolation("only a single SELECT may run"), 403),
        (ConnectorError("ledger_db timed out"), 502),
    ],
)
def test_probeowl_errors_map_to_statuses(
    app: FastAPI, client: TestClient, error: Exception, status: int
) -> None:
    @app.get("/boom")
    def boom() -> None:
        raise error

    response = client.get("/boom")
    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["detail"] == str(error)


def test_unexpected_error_hides_the_traceback(app: FastAPI, client: TestClient) -> None:
    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("password=s3cret in a driver message")

    response = client.get("/boom")
    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_JSON
    assert "s3cret" not in response.text
    assert "Traceback" not in response.text


def test_every_error_response_in_the_contract_is_problem_details(app: FastAPI) -> None:
    for operations in app.openapi()["paths"].values():
        for operation in operations.values():
            for code, response in operation["responses"].items():
                if code[0] in "45":
                    assert list(response["content"]) == [PROBLEM_JSON]
