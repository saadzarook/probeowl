# Author: @SaadZarook
"""Strip where connectors point and who logs in from what the API returns, stored runs included.

New failures carry fixed messages (connectors/failures.py). This is the second line: it covers
runs stored before those, and anything else that names a host, a login or a secret reference.

Evidence has to stay exact, so each part gets only what it can bear: an authored SQL statement
is not searched for logins, and result rows, which are the subject's data, lose only this
application's own hosts and URLs.
"""

import re
from collections.abc import Iterable
from typing import cast

from pydantic import JsonValue

from probeowl.model.check import LoadedApplication
from probeowl.model.connector import RestConnector
from probeowl.model.run import Evidence, HealthyAttempt, LocationResult, Run

HOST = "[host]"
LOGIN = "[login]"
SECRET = "[secret]"

_SECRET_REFERENCE = re.compile(r"\b(?:keyring:[^\s'\",;)]+|env:\w+)|\bvault://[^\s'\",;)]+")
_CONNECTION_VALUE = re.compile(
    r"\b(server|data source|uid|user id|pwd)=[^;)\s]+|\((host|port|service_name)=[^)]*\)",
    re.IGNORECASE,
)
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s'\",;)]+(/?)", re.IGNORECASE)
_IP_ADDRESS = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_NAME_AND_PORT = re.compile(r"\b[\w-]+(?:\.[\w-]+)+[:,]\d{2,5}\b")


def _without_authority(match: re.Match[str]) -> str:
    # A URL keeps its path, which says what was asked for; only where it went is removed.
    return "/" if match.group(1) else HOST


def _any_of(values: Iterable[str], suffix: str = "") -> re.Pattern[str] | None:
    # Longest first, so a host is not matched by a shorter one inside it. Whole names only, so a
    # login called sa leaves "unsafe" alone.
    names = "|".join(re.escape(v) for v in sorted(set(values), key=len, reverse=True))
    if not names:
        return None
    return re.compile(rf"(?<![\w.-])(?:{names}){suffix}(?![\w-])", re.IGNORECASE)


class Redactor:
    """Removes the bindings of the loaded applications, and the shape of any other, from text."""

    def __init__(self, applications: dict[str, LoadedApplication]):
        hosts: set[str] = set()
        logins: set[str] = set()
        secrets: set[str] = set()
        for app in applications.values():
            for connector in app.connectors.values():
                if isinstance(connector, RestConnector):
                    for rest in connector.environments.values():
                        hosts.add(str(rest.base_url.host))
                        if rest.secret:
                            secrets.add(rest.secret)
                else:
                    for sql in connector.environments.values():
                        hosts.add(sql.host)
                        logins.add(sql.user)
                        secrets.add(sql.secret)
        # A short host name takes the domain a driver added after it, and any port.
        self._hosts = _any_of(hosts, r"(?:\.[\w-]+)*(?:[:,]\d+)?")
        self._logins = _any_of(logins)
        self._secrets = _any_of(secrets)

    def text(self, value: str) -> str:
        """A message: an error, a reason, a warning, a guard."""
        value = self._replace(self._secrets, SECRET, _SECRET_REFERENCE.sub(SECRET, value))
        value = _CONNECTION_VALUE.sub(SECRET, value)
        value = self.query(value)
        value = _IP_ADDRESS.sub(HOST, _NAME_AND_PORT.sub(HOST, value))
        return self._replace(self._logins, LOGIN, value)

    def query(self, value: str) -> str:
        """A statement or request as sent: what it asked for stays exactly as it was."""
        return self._replace(self._hosts, HOST, _URL.sub(_without_authority, value))

    def data(self, value: JsonValue) -> JsonValue:
        """Parameters and rows: the subject's data, which lose only known hosts and URLs."""
        if isinstance(value, str):
            return self.query(value)
        if isinstance(value, list):
            return [self.data(item) for item in value]
        if isinstance(value, dict):
            return {key: self.data(item) for key, item in value.items()}
        return value

    def run(self, run: Run) -> Run:
        """The run as a response may show it."""
        return run.model_copy(
            update={
                "resolution": self._location(run.resolution) if run.resolution else None,
                "locations": [self._location(location) for location in run.locations],
                "skip_reason": self._optional(run.skip_reason),
                "stop_reason": self._optional(run.stop_reason),
                "healthy_attempt": self._attempt(run.healthy_attempt),
                "warnings": [self.text(warning) for warning in run.warnings],
                "expectations": [
                    e.model_copy(update={"detail": self._optional(e.detail)})
                    for e in run.expectations
                ],
            }
        )

    def _attempt(self, attempt: HealthyAttempt | None) -> HealthyAttempt | None:
        if attempt is None:
            return None
        return attempt.model_copy(
            update={
                "message": self._optional(attempt.message),
                "sample": self._evidence(attempt.sample) if attempt.sample else None,
            }
        )

    def _location(self, location: LocationResult) -> LocationResult:
        evidence = location.evidence
        return location.model_copy(
            update={
                "reason": self._optional(location.reason),
                "evidence": self._evidence(evidence) if evidence else None,
            }
        )

    def _evidence(self, evidence: Evidence) -> Evidence:
        rows = cast(JsonValue, evidence.rows)
        parameters = cast(JsonValue, evidence.parameters)
        return evidence.model_copy(
            update={
                "query": self.query(evidence.query),
                "parameters": self.data(parameters),
                "guards": [self.text(guard) for guard in evidence.guards],
                "rows": self.data(rows),
            }
        )

    def _optional(self, value: str | None) -> str | None:
        return self.text(value) if value is not None else None

    @staticmethod
    def _replace(pattern: re.Pattern[str] | None, shown: str, value: str) -> str:
        return pattern.sub(shown, value) if pattern is not None else value
