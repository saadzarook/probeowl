# Author: @SaadZarook
"""Fail if any response from a real owl serve names a host, port, login or secret reference.

Part of the M1, M2 and M3 exit checks, against the dev stack and real runs: the unit tests'
fake connectors never produce a driver's own text.
Usage: scan_responses.py <config folder> <server> [run id ...]
"""

import re
import sys
from pathlib import Path

import httpx

from probeowl.model.check import load_applications
from probeowl.model.connector import RestConnector

# In every response, whatever the bindings are.
ANY_BINDING = re.compile(r"\b(?:keyring:|env:PROBEOWL)|\b[a-z][a-z+.-]*://", re.IGNORECASE)
# Where result rows may hold a URL of the subject's own: not a connector's.
ROWS = re.compile(r'"rows":\[.*?\](?=,"rows_total")', re.DOTALL)


def binding_values(config: Path) -> set[str]:
    """Where each connector points and how it logs in."""
    values: set[str] = set()
    for app in load_applications(config).values():
        for connector in app.connectors.values():
            for environment in connector.environments:
                if isinstance(connector, RestConnector):
                    rest = connector.environments[environment]
                    values |= {str(rest.base_url), str(rest.base_url.host)}
                    values |= {rest.secret} if rest.secret else set()
                else:
                    sql = connector.environments[environment]
                    values |= {sql.host, sql.user, sql.secret}
                    values |= {f"{sql.host}:{sql.port}", f"{sql.host},{sql.port}"}
    return values


def paths(config: Path, runs: list[str]) -> list[str]:
    """Every read route that can carry evidence or an error, on what this config loaded."""
    found = ["/api/applications", "/api/applications/nope", *[f"/api/runs/{run}" for run in runs]]
    for name, app in load_applications(config).items():
        base = f"/api/applications/{name}"
        found += [base, f"{base}/environments", f"{base}/entities", f"{base}/probes"]
        found += [f"{base}/catalog/review", f"{base}/connectors?environment=nope"]
        found += [f"{base}/probes/{probe}/yaml" for probe in app.probes]
        found += [f"{base}/entities/{entity}/yaml" for entity in app.entities]
        for environment in app.application.environments:
            found += [
                f"{base}/connectors?environment={environment}",
                f"{base}/probes?environment={environment}",
                f"/api/runs?application={name}&environment={environment}",
            ]
    return found


def main(config: Path, server: str, runs: list[str]) -> int:
    values = binding_values(config)
    scanned = paths(config, runs)
    leaks = 0
    with httpx.Client(base_url=server, timeout=120) as api:
        for path in scanned:
            text = api.get(path).text
            leaked = {value for value in values if value in text}
            shaped = ANY_BINDING.search(ROWS.sub("", text))
            if shaped is not None:
                leaked.add(shaped.group(0))
            if leaked:
                print(f"FAIL: {path} returned {sorted(leaked)}", file=sys.stderr)
                leaks += 1
    print(f"Scanned {len(scanned)} responses, {len(runs)} of them real runs: {leaks} leaked")
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), sys.argv[2], sys.argv[3:]))
