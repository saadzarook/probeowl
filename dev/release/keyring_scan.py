# Author: @SaadZarook
"""Fail if the installed ProbeOwl could reach a keyring other than the system keychain.

    keyring_scan.py <tool environment>

Run by wheel_check.sh on the package as uv tool install put it. The same rule as
backend/tests/test_secrets.py, on what users get: no PROBEOWL_ALLOW_INSECURE_KEYRING, no
keyrings.alt import, and no use of keyring's default backend (a chain, or what
PYTHON_KEYRING_BACKEND names). Prints each file and match it finds; never reads a keychain.
"""

import re
import sys
from pathlib import Path

FORBIDDEN = re.compile(
    r"PROBEOWL_ALLOW_INSECURE_KEYRING"
    r"|^\s*(?:import|from)\s+keyrings\b"
    r"|\bget_keyring\s*\("
    r"|\bkeyring\.(?:get|set|delete)_password\b"
    r"|\bkeyring\.(?:get_credential|set_keyring|core)\b",
    re.M,
)


def main() -> int:
    packages = [p for p in Path(sys.argv[1]).rglob("site-packages/probeowl") if p.is_dir()]
    if len(packages) != 1:
        print(f"expected one installed probeowl package, found {len(packages)}", file=sys.stderr)
        return 1
    found = [
        f"{path}: {match.group(0).strip()}"
        for path in sorted(packages[0].rglob("*.py"))
        for match in FORBIDDEN.finditer(path.read_text(encoding="utf-8"))
    ]
    for line in found:
        print(line, file=sys.stderr)
    print(f"{len(found)} forbidden uses in {packages[0]}")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
