# Author: @SaadZarook
"""A REST lookup whose key is masked: the key shows nowhere its run is returned, stored or logged.

A SQL lookup binds its key. A REST lookup sends it in the request line, and the API may echo it in
its answer, under any name.
"""

import logging
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from typer.testing import CliRunner

from probeowl.cli import main as cli
from probeowl.connectors.rest import RestAdapter
from probeowl.engine.masking import MASKED, mask_text, mask_values
from probeowl.errors import ConnectorError
from probeowl.model import RestBinding
from probeowl.model.entity import Entity
from probeowl.server_log import configure_server_log, keep_urls_out_of_logs
from probeowl.store.audit import list_events
from probeowl.store.runs import get_run, save_run
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import EXAMPLES, Setup
from tests.test_pii_subject import everything_stored, key_is_pii
from tests.test_runs import finished, start

PAYMENT = "pay_7hq2m9"
TOPUP = "tp_91f2c7"
CARDS = ["C204518", "C198220", "C177031"]


class Api:
    """A real REST adapter over a transport that answers in this process."""

    def __init__(self, answer: Callable[[httpx.Request], httpx.Response]):
        self.calls: list[Any] = []  # what Setup's fakes have
        self._adapter = RestAdapter(
            RestBinding.model_validate({"base_url": "http://127.0.0.1:9/api/v1"}),
            None,
            transport=httpx.MockTransport(answer),
            sleep=lambda _: None,
        )

    def lookup(self, lookup: str, parameters: Any, max_rows: int, /) -> Any:
        return self._adapter.lookup(lookup, parameters, max_rows)

    def close(self) -> None:
        pass


def echo(request: httpx.Request) -> httpx.Response:
    """The key back under other names: its own id, a link, a key of an object, and in text."""
    key = request.url.path.rsplit("/", 1)[-1] or ""
    if "reference" in request.url.params:
        key = request.url.params["reference"]
    body = {
        "id": key,
        "status": "captured",
        "amount": "500.00",
        "currency": "USD",
        "href": f"https://pay.example/v1/{key}?x=1",
        "by_id": {key: {"upper": key.upper()}},
        "events": [{"text": f"captured {key} once"}],
    }
    return httpx.Response(200, json=body)


def change(setup: Setup, application: str, name: str, edit: Callable[[Entity], Entity]) -> None:
    entities = setup.applications[application].entities
    entities[name] = edit(entities[name])


def identifier_is_pii(setup: Setup, application: str, name: str, identifier: str) -> None:
    def edit(entity: Entity) -> Entity:
        found = dict(entity.identifiers)
        found[identifier] = found[identifier].model_copy(update={"pii": True})
        return entity.model_copy(update={"identifiers": found})

    change(setup, application, name, edit)


def masked_at_source(setup: Setup, application: str, name: str, location: str, field: str) -> None:
    """The field is in its own location's pii list, and is nobody's PII identifier."""

    def edit(entity: Entity) -> Entity:
        found = dict(entity.locations)
        source = found[location]
        found[location] = source.model_copy(update={"pii": [*source.pii, field]})
        return entity.model_copy(update={"locations": found})

    change(setup, application, name, edit)


# What echo answers besides status, amount and currency: shown only once exposed.
ECHOED = ["id", "href", "by_id", "events"]


def exposing(setup: Setup, application: str, name: str, location: str, fields: list[str]) -> None:
    """The REST location shows these fields too: the rest of its answer is masked."""

    def edit(entity: Entity) -> Entity:
        found = dict(entity.locations)
        source = found[location]
        found[location] = source.model_copy(update={"exposes": [*source.exposes, *fields]})
        return entity.model_copy(update={"locations": found})

    change(setup, application, name, edit)


def location(run: dict[str, Any], name: str) -> dict[str, Any]:
    [found] = [r for r in run["locations"] if r["location"] == name]
    return dict(found)


def nowhere(setup: Setup, api: Any, run: dict[str, Any], caplog: Any, values: list[str]) -> None:
    """The values are in no response, stored row, audit event or log record."""
    shown = {
        "response": api.get(f"/api/runs/{run['id']}").text,
        "list": api.get(
            "/api/runs",
            params={"application": run["application"], "environment": run["environment"]},
        ).text,
        "store": everything_stored(setup),
        "log": "\n".join(r.getMessage() for r in caplog.records if r.name != "httpx.test"),
    }
    with setup.store.connect() as conn:
        events = list_events(conn, LOCAL_WORKSPACE)
    shown["audit"] = "\n".join(e.model_dump_json() for e in events)
    leaked = {
        where: value
        for where, text in shown.items()
        for value in values
        if value.casefold() in text.casefold()
    }
    assert leaked == {}


@pytest.fixture(autouse=True)
def as_owl_serve_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> Any:
    """Every logger at its most talkative, then what owl serve sets: only that keeps URLs out."""
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.NOTSET)
    configure_server_log(tmp_path / "owl-serve.log")
    logging.getLogger("probeowl").propagate = True
    yield
    for handler in list(logging.getLogger("probeowl").handlers):
        logging.getLogger("probeowl").removeHandler(handler)
        handler.close()


@pytest.mark.parametrize("how", ["identifier", "source"])
def test_a_key_is_masked_in_the_request_line_and_in_what_the_api_echoes(
    setup: Setup, caplog: pytest.LogCaptureFixture, how: str
) -> None:
    setup.fakes["checkout_api"] = Api(echo)  # type: ignore[assignment]
    exposing(setup, "wallet", "topup", "payment", ECHOED)
    if how == "identifier":
        identifier_is_pii(setup, "wallet", "topup", "provider_payment_id")
    else:
        masked_at_source(setup, "wallet", "topup", "record", "provider_payment_id")
    api = setup.client()
    run = finished(api, start(api, value=TOPUP))
    payment = location(run, "payment")
    assert payment["outcome"] == "found"
    evidence = payment["evidence"]
    assert evidence["query"] == f"GET /api/v1/payments/{MASKED}"
    assert evidence["parameters"] == {"provider_payment_id": MASKED}
    [row] = evidence["rows"]
    assert row["id"] == MASKED and row["status"] == "captured" and row["amount"] == "500.00"
    assert row["href"].endswith(f"/v1/{MASKED}?x=1")
    assert row["by_id"] == {MASKED: {"upper": MASKED}}
    assert row["events"] == [{"text": f"captured {MASKED} once"}]
    nowhere(setup, api, run, caplog, [PAYMENT])


def test_a_root_key_that_is_pii_is_masked_in_a_fallbacks_query_string(
    setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    def only_by_reference(request: httpx.Request) -> httpx.Response:
        return echo(request) if "reference" in request.url.params else httpx.Response(404)

    setup.fakes["checkout_api"] = Api(only_by_reference)  # type: ignore[assignment]
    key_is_pii(setup)
    api = setup.client()
    run = finished(api, start(api, value=TOPUP))
    payment = location(run, "payment")
    assert payment["evidence"]["query"] == f"GET /api/v1/payments?reference={MASKED}"
    nowhere(setup, api, run, caplog, [TOPUP])


@pytest.mark.parametrize("how", ["identifier", "source"])
def test_a_fan_outs_key_is_masked_for_every_row(
    setup: Setup, caplog: pytest.LogCaptureFixture, how: str
) -> None:
    setup.fakes["door_controller_api"] = Api(echo)  # type: ignore[assignment]
    if how == "identifier":
        identifier_is_pii(setup, "card_access", "visitor", "card_id")
    else:
        masked_at_source(setup, "card_access", "visitor", "cards", "card_id")
    api = setup.client()
    run_id = start(api, application="card_access", probe="visitor_cards", value="VP00012931")
    run = finished(api, run_id)
    doors = [r for r in run["locations"] if r["location"] == "controller"]
    assert len(doors) >= 3
    assert {d["evidence"]["query"] for d in doors} == {f"GET /api/v1/cards/{MASKED}"}
    nowhere(setup, api, run, caplog, CARDS)


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_no_refusal_or_error_names_the_key(
    setup: Setup, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    setup.fakes["checkout_api"] = Api(  # type: ignore[assignment]
        lambda r: httpx.Response(status, text=f"no {PAYMENT}", headers={"X-Key": PAYMENT})
    )
    identifier_is_pii(setup, "wallet", "topup", "provider_payment_id")
    api = setup.client()
    run = finished(api, start(api, value=TOPUP))
    nowhere(setup, api, run, caplog, [PAYMENT])


@pytest.mark.parametrize("fan_out", [False, True])
def test_an_error_that_names_the_key_is_masked(
    setup: Setup, caplog: pytest.LogCaptureFixture, fan_out: bool
) -> None:
    class Failing(Api):
        def lookup(self, lookup: str, parameters: Any, max_rows: int, /) -> Any:
            key = parameters.get("card_id" if fan_out else "provider_payment_id")
            raise ConnectorError(f"could not get https://api.example/{key}")

    failing = Failing(echo)
    if fan_out:
        setup.fakes["door_controller_api"] = failing  # type: ignore[assignment]
        identifier_is_pii(setup, "card_access", "visitor", "card_id")
        more = {"application": "card_access", "probe": "visitor_cards", "value": "VP00012931"}
    else:
        setup.fakes["checkout_api"] = failing  # type: ignore[assignment]
        identifier_is_pii(setup, "wallet", "topup", "provider_payment_id")
        more = {"value": TOPUP}
    api = setup.client()
    run = finished(api, start(api, **more))
    reasons = [r["reason"] for r in run["locations"] if r["outcome"] == "error"]
    assert reasons and all(MASKED in reason for reason in reasons)
    nowhere(setup, api, run, caplog, CARDS if fan_out else [PAYMENT])


def test_a_failed_expectation_on_an_echoed_key_does_not_print_it(
    setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    setup.fakes["checkout_api"] = Api(echo)  # type: ignore[assignment]
    identifier_is_pii(setup, "wallet", "topup", "provider_payment_id")
    probe = (EXAMPLES / "wallet" / "probes" / "topup_completed.yaml").read_text(encoding="utf-8")
    draft = probe.replace("count(webhook) >= 1", "payment.id == 'other'")
    assert draft != probe
    api = setup.client()
    response = api.post(
        "/api/applications/wallet/probes/test-runs",
        json={
            "probe": draft,
            "environment": "uat",
            "value": TOPUP,
            "investigation": {"reason": "proactive_check"},
        },
    )
    assert response.status_code == 202, response.text
    run = finished(api, response.json()["id"])
    [failed] = [e for e in run["expectations"] if e["expectation"] == "payment.id == 'other'"]
    assert failed["result"] == "fail" and MASKED in failed["detail"]
    nowhere(setup, api, run, caplog, [PAYMENT])


def test_a_key_that_is_not_masked_stays_exactly_as_sent(setup: Setup) -> None:
    setup.fakes["checkout_api"] = Api(echo)  # type: ignore[assignment]
    exposing(setup, "wallet", "topup", "payment", ["id"])
    api = setup.client()
    run = finished(api, start(api, value=TOPUP))
    evidence = location(run, "payment")["evidence"]
    assert evidence["query"] == f"GET /api/v1/payments/{PAYMENT}"
    assert evidence["rows"][0]["id"] == PAYMENT


@pytest.mark.parametrize("value", ["a@b.example", "a/b c", "%41", "a+b", "é", "x"])
def test_the_request_line_is_masked_however_the_value_is_encoded(value: str) -> None:
    sent = Api(lambda r: httpx.Response(200, json={})).lookup(
        "GET /u/{{id}}/x?email={{id}}&n={{n}}", {"id": value, "n": 1}, 5
    )
    assert sent.masked_query is not None
    assert sent.masked_query({"id"}) == f"GET /api/v1/u/{MASKED}/x?email={MASKED}&n=1"


def test_masking_by_value_does_not_hide_what_merely_contains_a_short_one() -> None:
    assert mask_text("42 rows, 142 ms", {"42"}) == "42 rows, 142 ms"
    assert mask_text("42", {"42"}) == MASKED
    assert mask_values({"pay_7hq2m9_id": "integer"}, {"pay_7hq2m9"}) == {"pay_7hq2m9_id": "integer"}
    assert mask_values([{"n": 42, "m": 142, "ok": True, "none": None}], {"42"}) == [
        {"n": MASKED, "m": 142, "ok": True, "none": None}
    ]


# --- Logs ---------------------------------------------------------------------------------------


def test_owl_serve_keeps_the_http_clients_urls_out_of_its_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.DEBUG)
    keep_urls_out_of_logs()
    for name in ("httpx", "httpcore", "httpcore.http11"):
        assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING
    Api(echo).lookup("GET /payments/{{id}}", {"id": PAYMENT}, 5)
    assert PAYMENT not in caplog.text


def test_a_pasted_value_in_a_search_is_not_in_the_access_log(tmp_path: Path) -> None:
    app = FastAPI()

    @app.get("/api/search")
    def search(q: str) -> dict[str, str]:
        return {"ok": "yes"}

    seen: list[str] = []

    class Kept(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off"))
    # As owl serve does: ProbeOwl's log is set up first, then uvicorn sets up its own.
    keep_urls_out_of_logs()
    handler = Kept()
    logging.getLogger("uvicorn.access").addHandler(handler)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        httpx.get(f"http://127.0.0.1:{port}/api/search", params={"q": "someone@example.com"})
    finally:
        server.should_exit = True
        thread.join(10)
        logging.getLogger("uvicorn.access").removeHandler(handler)
    assert any("/api/search" in line for line in seen)
    assert not any("someone" in line for line in seen)


# --- Runs stored before this -------------------------------------------------------------------


def stored_before(setup: Setup, query: str, lookups: dict[str, str] | None = None) -> str:
    """A run as 0.2.3 stored it: its parameter masked, the key in its query, echo and detail."""
    setup.fakes["checkout_api"] = Api(echo)  # type: ignore[assignment]
    api = setup.client()
    run_id = finished(api, start(api, value=TOPUP))["id"]
    identifier_is_pii(setup, "wallet", "topup", "provider_payment_id")
    with setup.store.begin() as conn:
        run = get_run(conn, LOCAL_WORKSPACE, run_id)
        locations = []
        for each in run.locations:
            if each.location == "payment" and each.evidence is not None:
                names = lookups or {"provider_payment_id": MASKED}
                evidence = each.evidence.model_copy(
                    update={"query": query, "parameters": dict(names)}
                )
                each = each.model_copy(
                    update={"evidence": evidence, "reason": f"looked for {PAYMENT}"}
                )
            locations.append(each)
        details = [
            e.model_copy(update={"detail": f"got {PAYMENT}"}) if index == 0 else e
            for index, e in enumerate(run.expectations)
        ]
        update = {"locations": locations, "expectations": details}
        save_run(conn, LOCAL_WORKSPACE, run.model_copy(update=update))
    return str(run_id)


def owl_mask(setup: Setup, *more: str) -> Any:
    url = str(setup.store.url.render_as_string(hide_password=False))
    return CliRunner().invoke(
        cli.app, ["store", "mask-pii", str(setup_folder(setup)), "--url", url, *more]
    )


def setup_folder(setup: Setup) -> Path:
    """The examples on disk, with the payment ID marked PII: what mask-pii reads."""
    import shutil

    folder = Path(str(setup.store.url.database)).parent / "config"
    if not folder.exists():
        shutil.copytree(EXAMPLES, folder)
        path = folder / "wallet" / "entities" / "topup.yaml"
        text = path.read_text(encoding="utf-8")
        marked = text.replace(
            "via: record.provider_payment_id, unique: true",
            "via: record.provider_payment_id, unique: true, pii: true",
        )
        assert marked != text
        path.write_text(marked, encoding="utf-8")
    return folder


def test_an_old_run_is_masked_when_read_before_mask_pii_is_run(setup: Setup) -> None:
    run_id = stored_before(setup, f"GET /api/v1/payments/{PAYMENT}")
    text = setup.client().get(f"/api/runs/{run_id}").text
    assert PAYMENT not in text and f"GET /api/v1/payments/{MASKED}" in text


@pytest.mark.parametrize(
    ("query", "becomes"),
    [
        (f"GET /api/v1/payments/{PAYMENT}", f"GET /api/v1/payments/{MASKED}"),
        (f"GET /payments/{PAYMENT}", f"GET /payments/{MASKED}"),
        # The lookup was written differently then: nothing says which part is the key.
        (f"GET /api/v0/pay/{PAYMENT}/detail", f"GET {MASKED}"),
        (f"GET /api/v1/find?id={PAYMENT}", f"GET {MASKED}"),
    ],
)
def test_mask_pii_cleans_stored_queries_only_with_apply_and_leaves_the_audit(
    setup: Setup, query: str, becomes: str
) -> None:
    run_id = stored_before(setup, query)
    with setup.store.connect() as conn:
        audit = [e.model_dump_json() for e in list_events(conn, LOCAL_WORKSPACE)]

    dry = owl_mask(setup)
    assert dry.exit_code == 0, dry.output
    assert "payment evidence" in dry.output and "Nothing written" in dry.output
    assert PAYMENT not in dry.output
    assert f"{query}" in everything_stored(setup, but_the_audit=True)
    with setup.store.connect() as conn:
        assert [e.model_dump_json() for e in list_events(conn, LOCAL_WORKSPACE)] == audit

    applied = owl_mask(setup, "--apply")
    assert applied.exit_code == 0, applied.output
    assert PAYMENT not in applied.output
    with setup.store.connect() as conn:
        run = get_run(conn, LOCAL_WORKSPACE, run_id)
        events = [e.model_dump_json() for e in list_events(conn, LOCAL_WORKSPACE)]
    [payment] = [r for r in run.locations if r.location == "payment"]
    assert payment.evidence is not None and payment.evidence.query == becomes
    # What was there is untouched; only the masking itself is added.
    assert [e for e in events if e in audit] == audit and len(events) == len(audit) + 1
    assert "store_pii_masked" in "".join(e for e in events if e not in audit)
    if becomes != f"GET {MASKED}":
        # The line gave the key away, so its echo, the reason and the detail are cleaned too.
        assert PAYMENT not in everything_stored(setup, but_the_audit=True)
    again = owl_mask(setup, "--apply")
    assert "Nothing to mask." in again.output


def test_mask_pii_never_masks_the_wrong_part_of_a_fallbacks_line(setup: Setup) -> None:
    # The fallback's own key is not PII; its line must not be read as the lookup's.
    query = f"GET /api/v1/payments?reference={TOPUP}"
    run_id = stored_before(setup, query, {"topup_id": TOPUP})
    owl_mask(setup, "--apply")
    with setup.store.connect() as conn:
        run = get_run(conn, LOCAL_WORKSPACE, run_id)
    [payment] = [r for r in run.locations if r.location == "payment"]
    assert payment.evidence is not None and payment.evidence.query == query
