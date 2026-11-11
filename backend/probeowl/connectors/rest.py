# Author: @SaadZarook
"""A read-only REST client: GET only, values encoded, bounded in time, retries and rate.

A lookup is written in the entity as `GET /payments/{{provider_payment_id}}`. The template
is checked like probe SQL: only GET, only a path under the binding's base URL, and every
value URL-encoded in place, never pasted into the URL as text.
"""

import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

import httpx

from probeowl.connectors.lookup import LookupResult
from probeowl.errors import ConnectorError, GuardViolation
from probeowl.guards.result import cap_result
from probeowl.guards.statement import ParameterValue
from probeowl.model.connector import DEFAULT_ROW_CEILING, RestBinding

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_RETRY_STATUSES = {429, 500, 502, 503, 504}
REDACTED = "[redacted]"


@dataclass(frozen=True)
class RestLimits:
    """Runner limits for one REST connector."""

    timeout_s: float = 5.0
    retries: int = 2
    backoff_s: float = 0.5
    requests_per_minute: int = 100
    max_wait_s: float = 5.0  # longest wait for the rate budget or a Retry-After
    max_bytes: int = 1_000_000
    row_ceiling: int = DEFAULT_ROW_CEILING


@dataclass(frozen=True)
class Request:
    """A checked GET: the path and query parameters, values encoded."""

    path: str
    params: tuple[tuple[str, str], ...]
    used: dict[str, ParameterValue]


class RateBudget:
    """A token bucket per connector: at most n requests a minute, with a short wait."""

    def __init__(
        self,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._capacity = float(per_minute)
        self._tokens = float(per_minute)
        self._rate = per_minute / 60.0
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = threading.Lock()

    def take(self, max_wait_s: float) -> None:
        """Use one request, waiting up to max_wait_s; ConnectorError if the budget is spent."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
            self._updated = now
            wait = (1 - self._tokens) / self._rate if self._tokens < 1 else 0.0
            if wait > max_wait_s:
                raise ConnectorError(
                    f"the rate budget of {int(self._capacity)} requests a minute is used up"
                )
            self._tokens -= 1
        if wait > 0:
            self._sleep(wait)


def build_request(lookup: str, parameters: Mapping[str, object]) -> Request:
    """Check a lookup template and fill it with encoded values."""
    method, _, target = lookup.strip().partition(" ")
    if method.upper() != "GET":
        raise GuardViolation(f"only GET is sent; this lookup uses {method or 'nothing'}")
    target = target.strip()
    split = urlsplit(target)
    if split.scheme or split.netloc or not target.startswith("/") or target.startswith("//"):
        raise GuardViolation(f"a REST lookup is a path under the base URL, not {target!r}")
    used: dict[str, ParameterValue] = {}

    def fill(template: str, in_path: bool) -> str:
        def value(match: re.Match[str]) -> str:
            name = match.group(1)
            used[name] = _value(name, parameters)
            text = str(used[name])
            if in_path and text in {".", ".."}:
                raise GuardViolation(f"{{{{{name}}}}} would move up the path: {text!r}")
            # safe="" encodes / too, so a value is always exactly one path segment.
            return quote(text, safe="") if in_path else text

        return _PLACEHOLDER.sub(value, template)

    path = fill(split.path, in_path=True)
    params = tuple(
        (key, fill(val, in_path=False))
        for key, val in parse_qsl(split.query, keep_blank_values=True)
    )
    return Request(path=path, params=params, used=used)


def _value(name: str, parameters: Mapping[str, object]) -> ParameterValue:
    if name not in parameters:
        raise GuardViolation(f"the lookup uses {{{{{name}}}}}, which has no value")
    value = parameters[name]
    if value is None or not isinstance(value, str | int | float | bool):
        raise GuardViolation(f"{{{{{name}}}}} must be a single value, not {type(value).__name__}")
    return value


class RestAdapter:
    """A read-only connection to one REST API."""

    def __init__(
        self,
        binding: RestBinding,
        token: str | None,
        limits: RestLimits | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._limits = limits or RestLimits()
        self._sleep = sleep
        self._budget = RateBudget(self._limits.requests_per_minute, sleep=sleep)
        # Every value that came from a secret, so it can be taken out of anything returned.
        self._secrets = [token] if token else []
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=str(binding.base_url),
            headers=headers,
            timeout=self._limits.timeout_s,
            # Always verify the server's certificate; a binding has no way to turn it off.
            verify=True,
            # A redirect to another host would carry the token there.
            follow_redirects=False,
            transport=transport,
        )

    def lookup(self, lookup: str, parameters: Mapping[str, object], max_rows: int) -> LookupResult:
        """GET one resource; no rows when it does not exist (404)."""
        request = build_request(lookup, parameters)
        url = self._client.build_request("GET", request.path, params=request.params).url
        started_at = datetime.now(UTC)
        start = time.monotonic()
        try:
            rows = self._get(request)
        except ConnectorError as err:
            raise ConnectorError(self._redact(str(err))) from None
        # Whatever a location asks for, including one owl check would have refused.
        ceiling = min(max_rows, self._limits.row_ceiling)
        capped = cap_result(rows, ceiling, self._limits.max_bytes)
        return LookupResult(
            # The path as sent: where the API lives never leaves the backend.
            query=self._redact(f"GET {url.raw_path.decode('ascii')}"),
            parameters=self._redact(request.used),
            guards=self._guards(),
            # An API may echo the credential back; it is never stored.
            rows=self._redact(capped.rows),
            truncated=capped.truncated,
            started_at=started_at,
            duration_ms=round((time.monotonic() - start) * 1000),
        )

    def unreachable(self) -> str | None:
        """Why the API cannot be reached, or None if it answers. One GET of its base URL, no retry.

        A connector names no health path, and its base URL may well answer 404: any answer means
        it was reached, except one refusing the credentials or a server error.
        """
        self._budget.take(self._limits.max_wait_s)
        try:
            with self._client.stream("GET", "") as response:
                status = response.status_code
        except httpx.TransportError as err:
            # Only the error's kind, as for a lookup: its text could carry the request.
            return f"the API could not be reached: {type(err).__name__}"
        if status in {401, 403} or status >= 500:
            return _status_message(status)
        return None

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def _get(self, request: Request) -> list[dict[str, Any]]:
        for attempt in range(self._limits.retries + 1):
            last = attempt == self._limits.retries
            self._budget.take(self._limits.max_wait_s)
            try:
                status, body, retry_after = self._send(request)
            except httpx.TransportError as err:
                if last:
                    # Only the error's kind: its text could carry the request or the response,
                    # and a chained exception would print it in any logged traceback.
                    raise ConnectorError(
                        f"the API could not be reached: {type(err).__name__}"
                    ) from None
                self._sleep(self._limits.backoff_s * 2**attempt)
                continue
            if status == 404:
                return []
            if status in _RETRY_STATUSES and not last:
                self._sleep(self._retry_wait(attempt, retry_after))
                continue
            if status != 200:
                raise ConnectorError(_status_message(status))
            return _rows(body)
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _send(self, request: Request) -> tuple[int, bytes, str | None]:
        with self._client.stream("GET", request.path, params=request.params) as response:
            body = bytearray()
            # Stop reading once the body is larger than allowed.
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > self._limits.max_bytes:
                    raise ConnectorError(
                        f"the response is larger than {self._limits.max_bytes} bytes"
                    )
            return response.status_code, bytes(body), response.headers.get("Retry-After")

    def _retry_wait(self, attempt: int, retry_after: str | None) -> float:
        wait = self._limits.backoff_s * 2**attempt
        if retry_after is not None and retry_after.isdigit():
            if float(retry_after) > self._limits.max_wait_s:
                raise ConnectorError(f"the API asked to retry after {retry_after} s")
            wait = float(retry_after)
        return wait

    def _redact(self, value: Any) -> Any:
        """value with every secret replaced, inside strings, lists and dicts."""
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, REDACTED)
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value

    def _guards(self) -> list[str]:
        return rest_guards(self._limits)


def rest_guards(limits: RestLimits | None = None) -> list[str]:
    """The guards a REST lookup runs under."""
    limits = limits or RestLimits()
    return [
        "GET only",
        "values URL-encoded, never interpolated",
        "TLS certificate verified",
        "no redirects",
        "credentials redacted",
        f"timeout {limits.timeout_s:g} s",
        f"{limits.retries} retries on 429, 5xx and network errors",
        f"rate budget {limits.requests_per_minute} requests a minute",
        f"row ceiling {limits.row_ceiling}",
    ]


def _status_message(status: int) -> str:
    if status in {401, 403}:
        return f"the API refused the credentials (HTTP {status}); check the binding's secret"
    if 300 <= status < 400:
        return f"the API answered with a redirect (HTTP {status}), which ProbeOwl does not follow"
    return f"the API answered HTTP {status}"


def _rows(body: bytes) -> list[dict[str, Any]]:
    try:
        data = json.loads(body) if body else None
    except json.JSONDecodeError as err:
        raise ConnectorError("the API answered with something other than JSON") from err
    if data is None:
        return []
    items = data if isinstance(data, list) else [data]
    # A list of plain values becomes rows with one field each.
    return [item if isinstance(item, dict) else {"value": item} for item in items]
