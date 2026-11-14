# Author: @SaadZarook
"""verify.sh's test-only entries in this machine's keychain, under ProbeOwl's own service.

    sweep          remove every probeowl/probeowl_test_* entry: what a run killed hard left behind
    exists <name>  exit 0 if probeowl/<name> is there
    delete <name>  remove probeowl/<name>, and only it

Only names starting with probeowl_test_ are ever touched; a real binding's entry never is. Each
removed name is printed, never a value: nothing here reads a password. Windows only, as is
verify.sh. Run with backend/.venv's Python, where keyring is installed.
"""

import sys

from win32ctypes.pywin32 import pywintypes, win32cred

SERVICE = "probeowl"
PREFIX = "probeowl_test_"
NOT_FOUND = 1168


def _test_name(name: str) -> str:
    if not name.startswith(PREFIX) or "@" in name:
        sys.exit(f"refusing {name!r}: only {PREFIX}* entries are verify.sh's to touch")
    return name


def _targets(name: str) -> list[str]:
    """Where keyring's Windows backend may keep probeowl/<name>: the service, or name@service."""
    found = []
    for credential in win32cred.CredEnumerate():
        if credential["Type"] != win32cred.CRED_TYPE_GENERIC:
            continue
        target, user = credential["TargetName"], credential["UserName"]
        if (target == SERVICE and user == name) or target == f"{name}@{SERVICE}":
            found.append(target)
    return found


def _test_entries() -> dict[str, str]:
    """Every test-only entry, as {target: name}. Reads names only, never a credential's blob."""
    found = {}
    for credential in win32cred.CredEnumerate():
        if credential["Type"] != win32cred.CRED_TYPE_GENERIC:
            continue
        target, user = credential["TargetName"], credential["UserName"] or ""
        if target == SERVICE and user.startswith(PREFIX):
            found[target] = user
        elif target.endswith(f"@{SERVICE}") and target.startswith(PREFIX):
            found[target] = target.removesuffix(f"@{SERVICE}")
    return found


def _delete(target: str) -> None:
    try:
        win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC)
    except pywintypes.error as error:
        if error.winerror != NOT_FOUND:  # gone already is what was wanted
            raise


def sweep() -> None:
    """Remove every test-only entry, naming each one."""
    entries = _test_entries()
    for target, name in entries.items():
        _delete(target)
        print(f"keychain: removed leftover {SERVICE}/{name}")
    if not entries:
        print(f"keychain: no leftover {SERVICE}/{PREFIX}* entries")


def main() -> None:
    """Run one command."""
    match sys.argv[1:]:
        case ["sweep"]:
            sweep()
        case ["exists", name]:
            sys.exit(0 if _targets(_test_name(name)) else 1)
        case ["delete", name]:
            for target in _targets(_test_name(name)):
                _delete(target)
            sys.exit(1 if _targets(name) else 0)
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main()
