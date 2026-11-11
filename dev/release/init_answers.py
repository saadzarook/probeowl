# Author: @SaadZarook
"""owl init end to end with scripted answers, inside the Python of an installed ProbeOwl.

    init_answers.py <case> <folder> <store url>      case: uat (UAT only, no Production) or prod-uat

Run by wheel_check.sh with the tool environment's own Python, so the Typer and click it imports
are the ones `uv tool install` resolved, not the lock's. Answers go in through Typer's test
runner: on Windows a password prompt reads the console, not a pipe, so piping into owl.exe hangs.
The keyring is the check's own file, never the system keychain (wheel_check.sh sets it up).
Every binding is on port 1 of 127.0.0.1, where nothing listens: owl check and owl serve find no
database to try, so the check never signs in anywhere.
"""

import sys

from typer.testing import CliRunner

from probeowl.cli.main import app

PASSWORD = "wheel-check-not-a-real-password"


def answers(case: str, folder: str) -> list[str]:
    """Every answer, in the order owl init asks; an empty one takes the default."""
    if case == "uat":
        return [
            folder, "access", "", "access-team",
            "uat", "",  # environments; Production: none, by Enter (#248)
            "",  # UAT's title
            "mariadb", "access_db",
            "", "1", "access_uat", "probeowl_ro", PASSWORD,  # port 1: nothing there
            "n", "", "n",  # not a replica; TLS; verify the certificate
            "y", "n", "n",  # create the store; no test; no import
        ]  # fmt: skip
    if case == "prod-uat":
        return [
            folder, "wallet", "", "payments-team",
            "", "",  # environments prod,uat; Production: prod
            "", "",  # their titles
            "", "wallet_db",  # sqlserver
            "", "1", "", "probeowl_ro", PASSWORD, "y", "n",  # prod on port 1: a replica, verify
            "", "", "wallet_uat", "", "", "n", "n",  # uat: port 1 as before, same login and password
            "y", "n", "n",  # create the store; no test; no import
        ]  # fmt: skip
    raise SystemExit(f"unknown case {case}: uat or prod-uat")


def main() -> int:
    case, folder, store = sys.argv[1:4]
    result = CliRunner().invoke(
        app, ["init"], input="\n".join(answers(case, folder)) + "\n", env={"PROBEOWL_STORE_URL": store}
    )
    print(result.output)
    if result.exit_code != 0:
        print(f"owl init ({case}) exited {result.exit_code}: {result.exception!r}", file=sys.stderr)
        if result.exc_info:
            import traceback

            traceback.print_exception(*result.exc_info)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
