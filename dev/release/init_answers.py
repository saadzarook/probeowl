# Author: @SaadZarook
"""owl init end to end with scripted answers, inside the Python of an installed ProbeOwl.

    init_answers.py <case> <folder> <store url>      case: uat (UAT only, no Production) or prod-uat

Run by wheel_check.sh with the tool environment's own Python, so the Typer and click it imports
are the ones `uv tool install` resolved, not the lock's. Answers go in through Typer's test
runner: on Windows a password prompt reads the console, not a pipe, so piping into owl.exe hangs.
Passwords go to this machine's system keychain, as a user's do, under probeowl_test_* names only:
each case removes its entries before and after, and checks owl init saved them there.
Every binding is on port 1 of 127.0.0.1, where nothing listens: owl check and owl serve find no
database to try, so the check never signs in anywhere.
"""

import sys

from typer.testing import CliRunner

from probeowl.cli.main import app
from probeowl.connector_files import KEYCHAIN_SERVICE
from probeowl.secrets import delete_keychain, read_keychain

PASSWORD = "wheel-check-not-a-real-password"
# The keychain entries each case's owl init writes: probeowl_test_* names only.
ENTRIES = {
    "uat": ("probeowl_test_access_db.uat",),
    "prod-uat": ("probeowl_test_wallet_db.prod", "probeowl_test_wallet_db.uat"),
}


def answers(case: str, folder: str) -> list[str]:
    """Every answer, in the order owl init asks; an empty one takes the default."""
    if case == "uat":
        return [
            folder, "access", "", "access-team",
            "uat", "",  # environments; Production: none, by Enter (#248)
            "",  # UAT's title
            "mariadb", "probeowl_test_access_db",
            "", "1", "access_uat", "probeowl_ro", PASSWORD,  # port 1: nothing there
            "n", "", "n",  # not a replica; TLS; verify the certificate
            "y", "n", "n",  # create the store; no test; no import
        ]  # fmt: skip
    if case == "prod-uat":
        return [
            folder, "wallet", "", "payments-team",
            "", "",  # environments prod,uat; Production: prod
            "", "",  # their titles
            "", "probeowl_test_wallet_db",  # sqlserver
            "", "1", "", "probeowl_ro", PASSWORD, "y", "n",  # prod on port 1: a replica, verify
            "", "", "wallet_uat", "", "", "n", "n",  # uat: port 1 as before, same login and password
            "y", "n", "n",  # create the store; no test; no import
        ]  # fmt: skip
    raise SystemExit(f"unknown case {case}: uat or prod-uat")


def main() -> int:
    case, folder, store = sys.argv[1:4]
    names = ENTRIES[case]
    _remove(names)  # what a run stopped part-way left behind
    try:
        code = _init(case, folder, store)
        if code != 0:
            return code
        missing = [n for n in names if read_keychain(KEYCHAIN_SERVICE, n) != PASSWORD]
        if missing:
            print(f"not in the system keychain: {', '.join(missing)}", file=sys.stderr)
            return 1
        print(f"saved in the system keychain: {', '.join(names)}")
        return 0
    finally:
        _remove(names)


def _remove(names: tuple[str, ...]) -> None:
    for name in names:
        if not name.startswith("probeowl_test_"):
            raise SystemExit(f"refusing {name}: only probeowl_test_* entries are this check's")
        delete_keychain(KEYCHAIN_SERVICE, name)


def _init(case: str, folder: str, store: str) -> int:
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
