# Author: @SaadZarook
"""The REST connector against a mock transport: what is sent, and how answers are handled."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from probeowl.connectors.rest import RateBudget, RestAdapter, RestLimits, build_request
from probeowl.errors import ConnectorError, GuardViolation
from probeowl.model import RestBinding

PAYMENT = {"id": "pay_7hq2m9", "status": "captured", "amount": "500.00", "currency": "USD"}


class Api:
    """A mock API that answers from a list and records what it was sent."""

    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def adapter(
    api: Api, token: str | None = None, sleeps: list[float] | None = None, **limits: Any
) -> RestAdapter:
    recorded = sleeps if sleeps is not None else []
    return RestAdapter(
        RestBinding(base_url="http://127.0.0.1:8081/v1"),
        token,
        RestLimits(**limits),
        transport=httpx.MockTransport(api),
        sleep=recorded.append,
    )


def ok(body: Any) -> httpx.Response:
    return httpx.Response(200, json=body)


# --- What may be sent ---------------------------------------------------------------------


def test_values_fill_the_path_and_query_encoded() -> None:
    request = build_request(
        "GET /payments/{{provider_payment_id}}?reference={{topup_id}}&limit=1",
        {"provider_payment_id": "pay_7hq2m9", "topup_id": "tp 91/f2c7"},
    )
    assert request.path == "/payments/pay_7hq2m9"
    assert request.params == (("reference", "tp 91/f2c7"), ("limit", "1"))
    assert request.used == {"provider_payment_id": "pay_7hq2m9", "topup_id": "tp 91/f2c7"}


@pytest.mark.parametrize(
    ("lookup", "match"),
    [
        ("POST /payments", "only GET"),
        ("DELETE /payments/{{id}}", "only GET"),
        ("/payments/{{id}}", "only GET"),
        ("GET http://evil.example/payments/{{id}}", "a path under the base URL"),
        ("GET //evil.example/payments", "a path under the base URL"),
        ("GET payments/{{id}}", "a path under the base URL"),
        ("GET /payments/{{missing}}", "has no value"),
    ],
)
def test_a_lookup_that_is_not_a_plain_get_under_the_base_url_is_refused(
    lookup: str, match: str
) -> None:
    with pytest.raises(GuardViolation, match=match):
        build_request(lookup, {"id": "pay_1"})


@pytest.mark.parametrize("value", [".", ".."])
def test_a_value_cannot_move_up_the_path(value: str) -> None:
    with pytest.raises(GuardViolation, match="would move up the path"):
        build_request("GET /payments/{{id}}", {"id": value})


@pytest.mark.parametrize("value", [None, ["a"], {"a": 1}])
def test_a_value_must_be_a_single_value(value: object) -> None:
    with pytest.raises(GuardViolation, match="must be a single value"):
        build_request("GET /payments/{{id}}", {"id": value})


@pytest.mark.parametrize(
    "hostile",
    ["../../admin", "pay_1/../../admin", "pay_1?admin=true", "pay_1#x", "%2e%2e", "a b/c"],
)
def test_a_hostile_id_stays_one_path_segment(hostile: str) -> None:
    api = Api(ok(PAYMENT))
    adapter(api).lookup("GET /payments/{{id}}", {"id": hostile}, 1)
    [sent] = api.requests
    assert sent.url.raw_path.decode().split("?")[0].count("/") == 3  # /v1/payments/<one segment>
    assert sent.url.path.startswith("/v1/payments/")
    assert not sent.url.query
    assert sent.url.host == "127.0.0.1"


# --- What comes back ----------------------------------------------------------------------


def test_a_found_resource_is_one_row_with_its_evidence() -> None:
    api = Api(ok(PAYMENT))
    result = adapter(api).lookup("GET /payments/{{id}}", {"id": "pay_7hq2m9"}, 1)
    assert result.rows == [PAYMENT]
    assert result.truncated is None
    assert result.query == "GET /v1/payments/pay_7hq2m9"  # never where the API lives
    assert result.parameters == {"id": "pay_7hq2m9"}
    assert "GET only" in result.guards
    assert api.requests[0].method == "GET"


def test_a_list_is_many_rows_and_the_cap_is_reported() -> None:
    api = Api(ok([PAYMENT] * 7))
    result = adapter(api).lookup("GET /payments?reference={{id}}", {"id": "tp_1"}, 5)
    assert len(result.rows) == 5
    assert result.truncated == "more than 5 rows"


def test_plain_values_become_rows_with_one_field() -> None:
    result = adapter(Api(ok(["a", "b"]))).lookup("GET /codes", {}, 5)
    assert result.rows == [{"value": "a"}, {"value": "b"}]


def test_not_found_is_no_rows_not_an_error() -> None:
    result = adapter(Api(httpx.Response(404))).lookup("GET /payments/{{id}}", {"id": "x"}, 1)
    assert result.rows == []


def test_the_token_is_sent_as_a_header_and_appears_nowhere_else() -> None:
    api = Api(ok(PAYMENT))
    result = adapter(api, token="s3cret-token").lookup("GET /payments/{{id}}", {"id": "p"}, 1)
    assert api.requests[0].headers["Authorization"] == "Bearer s3cret-token"
    assert "s3cret-token" not in json.dumps([result.query, result.guards, result.parameters])


# --- Failures, retries and budgets --------------------------------------------------------


def lookup_fails(api: Api, match: str, **limits: Any) -> list[float]:
    sleeps: list[float] = []
    with pytest.raises(ConnectorError, match=match):
        adapter(api, sleeps=sleeps, **limits).lookup("GET /payments/{{id}}", {"id": "p"}, 1)
    return sleeps


@pytest.mark.parametrize("status", [401, 403])
def test_refused_credentials_are_an_error_without_retry(status: int) -> None:
    api = Api(httpx.Response(status))
    lookup_fails(api, "refused the credentials")
    assert len(api.requests) == 1


def test_a_redirect_is_not_followed() -> None:
    api = Api(httpx.Response(302, headers={"Location": "http://evil.example/steal"}))
    lookup_fails(api, "redirect")
    assert len(api.requests) == 1


def test_a_server_error_is_retried_with_backoff_then_succeeds() -> None:
    api = Api(httpx.Response(503), httpx.Response(502), ok(PAYMENT))
    sleeps: list[float] = []
    result = adapter(api, sleeps=sleeps).lookup("GET /payments/{{id}}", {"id": "p"}, 1)
    assert result.rows == [PAYMENT]
    assert sleeps == [0.5, 1.0]
    assert len(api.requests) == 3


def test_a_server_error_that_lasts_is_an_error_after_the_retries() -> None:
    api = Api(httpx.Response(503))
    lookup_fails(api, "HTTP 503", retries=2)
    assert len(api.requests) == 3


def test_retry_after_is_honoured_when_short() -> None:
    api = Api(httpx.Response(429, headers={"Retry-After": "2"}), ok(PAYMENT))
    sleeps: list[float] = []
    adapter(api, sleeps=sleeps).lookup("GET /payments/{{id}}", {"id": "p"}, 1)
    assert sleeps == [2.0]


def test_a_long_retry_after_is_an_error_not_a_long_wait() -> None:
    api = Api(httpx.Response(429, headers={"Retry-After": "60"}))
    assert lookup_fails(api, "asked to retry after 60 s") == []


def test_network_errors_are_retried_then_an_error() -> None:
    api = Api(httpx.ConnectError("refused"))
    sleeps = lookup_fails(api, "could not be reached: ConnectError")
    assert len(api.requests) == 3
    assert sleeps == [0.5, 1.0]


def test_a_timeout_is_an_error() -> None:
    lookup_fails(Api(httpx.ReadTimeout("slow")), "could not be reached: ReadTimeout", retries=0)


def test_an_answer_that_is_not_json_is_an_error() -> None:
    lookup_fails(Api(httpx.Response(200, text="<html>oops</html>")), "other than JSON")


def test_a_body_larger_than_allowed_is_an_error() -> None:
    lookup_fails(Api(ok({"x": "y" * 5000})), "larger than 1000 bytes", max_bytes=1000)


def fake_clock() -> tuple[list[float], Callable[[], float]]:
    now = [0.0]
    return now, lambda: now[0]


def test_the_rate_budget_allows_its_requests_then_waits_then_refuses() -> None:
    now, clock = fake_clock()
    sleeps: list[float] = []
    budget = RateBudget(per_minute=2, clock=clock, sleep=sleeps.append)
    budget.take(max_wait_s=0)
    budget.take(max_wait_s=0)
    with pytest.raises(ConnectorError, match="rate budget of 2 requests a minute is used up"):
        budget.take(max_wait_s=5)  # the next token is 30 s away
    now[0] = 25.0
    budget.take(max_wait_s=10)  # 5 s away: waits
    assert sleeps == [pytest.approx(5.0)]


def test_each_connector_has_its_own_budget() -> None:
    first = adapter(Api(ok(PAYMENT)), requests_per_minute=1, max_wait_s=0)
    second = adapter(Api(ok(PAYMENT)), requests_per_minute=1, max_wait_s=0)
    first.lookup("GET /payments/{{id}}", {"id": "p"}, 1)
    second.lookup("GET /payments/{{id}}", {"id": "p"}, 1)
    with pytest.raises(ConnectorError, match="rate budget"):
        first.lookup("GET /payments/{{id}}", {"id": "p"}, 1)


def test_every_example_rest_lookup_is_a_valid_get() -> None:
    # The lookups in examples/ that use a REST connector.
    for lookup in (
        "GET /payments/{{provider_payment_id}}",
        "GET /payments?reference={{topup_id}}",
        "GET /cards/{{card_id}}",
    ):
        build_request(lookup, {"provider_payment_id": "p", "topup_id": "t", "card_id": "c"})


# --- Credentials and TLS ------------------------------------------------------------------

# Made up: the tests prove it never reaches evidence or a log.
TOKEN = "tok_s3cret_9f2b"  # gitleaks:allow


def stored(result: Any) -> str:
    """Everything a lookup result would put in evidence, as one string."""
    return json.dumps(
        [result.query, result.parameters, result.guards, result.rows, result.truncated],
        default=str,
    )


def test_a_token_echoed_back_by_the_api_is_never_stored() -> None:
    echo = httpx.Response(
        200,
        json={"id": "pay_1", "seen_auth": f"Bearer {TOKEN}", "nested": [{"key": TOKEN}]},
        headers={"X-Echo": TOKEN},
    )
    api = Api(echo)
    result = adapter(api, token=TOKEN).lookup("GET /payments/{{id}}", {"id": "pay_1"}, 1)
    assert api.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in stored(result)
    assert result.rows == [
        {"id": "pay_1", "seen_auth": "Bearer [redacted]", "nested": [{"key": "[redacted]"}]}
    ]
    assert "credentials redacted" in result.guards


def test_a_token_is_never_in_an_error_or_its_traceback() -> None:
    api = Api(httpx.ConnectError(f"connection to http://user:{TOKEN}@api failed"))
    with pytest.raises(ConnectorError) as failure:
        adapter(api, token=TOKEN, retries=0).lookup("GET /payments/{{id}}", {"id": "p"}, 1)
    assert TOKEN not in str(failure.value)
    # Nothing chained: a logged traceback would print the cause's text.
    assert failure.value.__cause__ is None
    assert failure.value.__context__ is None or TOKEN not in str(failure.value.__context__)


def test_tls_verification_is_always_on(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []
    client = httpx.Client

    def recording_client(**kwargs: Any) -> httpx.Client:
        created.append(kwargs)
        return client(**kwargs)

    monkeypatch.setattr(httpx, "Client", recording_client)
    RestAdapter(RestBinding(base_url="https://payments.example"), TOKEN).close()
    assert created[0]["verify"] is True
    assert "TLS certificate verified" in adapter(Api(ok(PAYMENT)))._guards()


@pytest.mark.parametrize("field", ["verify", "tls_verify", "insecure", "trust_server_certificate"])
def test_a_rest_binding_cannot_turn_tls_verification_off(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RestBinding.model_validate({"base_url": "https://payments.example", field: False})


# --- Test connection ------------------------------------------------------------------------


@pytest.mark.parametrize("status", [200, 301, 404, 405])
def test_any_answer_means_the_api_was_reached(status: int) -> None:
    # A connector names no health path, so its base URL may well answer 404.
    api = Api(httpx.Response(status, json={}))
    assert adapter(api).unreachable() is None
    assert len(api.requests) == 1
    assert api.requests[0].method == "GET"


@pytest.mark.parametrize("status", [401, 403])
def test_refused_credentials_are_not_a_working_connection(status: int) -> None:
    found = adapter(Api(httpx.Response(status, json={}))).unreachable()
    assert found == f"the API refused the credentials (HTTP {status}); check the binding's secret"


def test_a_server_error_is_not_retried_for_a_connection_test() -> None:
    api = Api(httpx.Response(503, json={}))
    assert adapter(api).unreachable() == "the API answered HTTP 503"
    assert len(api.requests) == 1


def test_a_network_failure_says_only_its_kind() -> None:
    failure = httpx.ConnectError("https://secret-host.example token=abc")
    assert adapter(Api(failure)).unreachable() == "the API could not be reached: ConnectError"


def test_a_redirect_is_not_followed_by_a_connection_test() -> None:
    moved = httpx.Response(302, headers={"Location": "https://elsewhere.example/"})
    api = Api(moved)
    assert adapter(api).unreachable() is None
    # The token goes only to the binding's own host, never to where a redirect points.
    assert [request.url.host for request in api.requests] == ["127.0.0.1"]


def test_a_lookup_never_returns_more_than_the_row_ceiling() -> None:
    rows = [{"id": n} for n in range(10)]
    found = adapter(Api(ok(rows)), row_ceiling=3).lookup("GET /topups", {}, 100)
    assert len(found.rows) == 3
    assert found.truncated == "more than 3 rows"
