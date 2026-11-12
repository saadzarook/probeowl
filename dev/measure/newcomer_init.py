# Author: @SaadZarook
"""owl init as a newcomer answers it for the card example, timed and counted.

    newcomer_init.py <folder> <result.json>

Run by newcomer_baseline.sh with the installed tool's own Python, so it measures the release as a
user installs it. The store comes from PROBEOWL_STORE_URL and the read-only password from
PROBEOWL_NATIVE_RO_PASSWORD; neither is printed or written to the result. Answers go in through
Typer's test runner: on Windows a password prompt reads the console, not a pipe.
"""

import json
import os
import sys
import time

from typer.testing import CliRunner

from probeowl.cli.main import app

APPLICATION = "card_baseline"


def answers(folder: str, password: str) -> list[str]:
    """Every answer, in the order owl init asks for the native MariaDB; "" takes the default."""
    return [
        folder, APPLICATION, "", "access-team",
        "uat", "",  # environments; no Production
        "",  # UAT's title
        "mariadb", "",  # type; connector id: the suggested card_baseline_mariadb
        "", "", "access_uat_test", "probeowl_test_ro", password,  # host and port by default
        "n",  # not a replica
        "", "y", "the native test server's own certificate",  # TLS; trust its certificate, and why
        "y", "y", "y",  # create the store; test; import
    ]  # fmt: skip


def main() -> int:
    folder, out = sys.argv[1:3]
    password = os.environ.get("PROBEOWL_NATIVE_RO_PASSWORD", "")
    if not password:
        print("no PROBEOWL_NATIVE_RO_PASSWORD", file=sys.stderr)
        return 1
    given = answers(folder, password)
    started = time.monotonic()
    result = CliRunner().invoke(app, ["init"], input="\n".join(given) + "\n")
    seconds = time.monotonic() - started
    # The output echoes no password: owl init prompts for it hidden.
    print(result.output.replace(password, "<password>"))
    if result.exit_code != 0:
        failure = type(result.exception).__name__
        print(f"owl init exited {result.exit_code}: {failure}", file=sys.stderr)
        return 1
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "answers": len(given),
                "defaults": sum(1 for a in given if a == ""),
                # The password is left out: its length differs per machine and is not published.
                "characters": sum(len(a) for a in given if a != password),
                "seconds": round(seconds, 1),
            },
            f,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
