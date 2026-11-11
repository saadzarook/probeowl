# Author: @SaadZarook
"""Save probes through owl serve against the dev stack: part of the M3 exit check."""

import sys
from pathlib import Path

import httpx

from probeowl.api.local_only import HEADER

GOOD = "http://127.0.0.1:7878"  # serves a copy of the examples
BAD = "http://127.0.0.1:7879"  # the same, with a ledger lookup that has no time filter or key
PROBE = "/api/applications/wallet/probes/topup_completed"
FILE = "wallet/probes/topup_completed.yaml"
REJECTED = "ledger_entries is a Transaction table: filter on its time column posted_at"


def put(server: str, text: str, base: str | None = None) -> httpx.Response:
    body = {"probe": text, "environment": "uat"} | ({"base": base} if base else {})
    return httpx.put(server + PROBE, json=body, headers={HEADER: "1"}, timeout=120)


def main(config: Path) -> None:
    path = config / FILE
    text = path.read_text(encoding="utf-8")
    edited = text.replace("count(webhook) >= 1", "count(webhook) >= 1  # saved by the exit check")
    assert edited != text

    print("== a query on a Transaction table without a time filter cannot be saved")
    refused = put(BAD, text)
    print(refused.status_code, refused.json()["detail"])
    assert refused.status_code == 422, refused.text
    assert REJECTED in str(refused.json()["errors"]), refused.text

    print("== a probe that fails a save check cannot be written")
    broken = put(GOOD, text.replace("count(webhook)", "count(nowhere)"))
    assert broken.status_code == 422, broken.text
    assert path.read_text(encoding="utf-8") == text

    print("== a good probe: the diff first, nothing written yet, plans read in UAT")
    preview = put(GOOD, edited)
    assert preview.status_code == 200, preview.text
    change = preview.json()
    for check in change["report"]["checks"]:
        print(
            f"  {check['status']:<4} {check['check']}: {check['location'] or ''} {check['message']}"
        )
    assert change["written"] is False and change["path"] == FILE
    assert "+  - expect: count(webhook) >= 1  # saved by the exit check" in change["diff"]
    assert path.read_text(encoding="utf-8") == text
    planned = {c["check"] for c in change["report"]["checks"] if c["environment"] == "uat"}
    assert {"wallet_db plan · SQL Server", "ledger_db plan · Oracle"} <= planned, planned
    # The dev stack's logins can read plans: SQL Server's SHOWPLAN grant is seeded.
    assert change["report"]["plans_not_run"] == 0, change["report"]

    print("== then exactly that change")
    written = put(GOOD, edited, change["base"])
    assert written.status_code == 200, written.text
    assert written.json()["written"] is True
    assert path.read_text(encoding="utf-8") == edited

    print("== a second write from the same preview is refused")
    assert put(GOOD, edited + "# again\n", change["base"]).status_code == 409


if __name__ == "__main__":
    main(Path(sys.argv[1]))
