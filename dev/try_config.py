# Author: @SaadZarook
"""Copy examples/ for dev/try.sh, with every password in the keychain under probeowl-try/.

    try_config.py <examples> <out> docker|native

Trying the examples never touches the keychain entries a real connector uses (probeowl/...):
each binding's secret becomes keyring:probeowl-try/<connector>.<env>, and the read-only
password, from $TRY_RO_PASSWORD, is saved there. With native, databases and logins get the
native setup's test names, as dev/native/make_config.py gives them. It refuses to finish on
any binding that is not on 127.0.0.1.
"""

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from probeowl.secrets import save_secret

sys.path.insert(0, str(Path(__file__).resolve().parent / "native"))
# dev/native is not a package: make_config is found through the path set above.
from make_config import NAMES, RO_LOGIN

TRY_SERVICE = "probeowl-try"
_LOCAL_URL = re.compile(r"^https?://127\.0\.0\.1(:\d+)?(/|$)")


def try_binding(connector: str, env: str, binding: dict[str, Any], native: bool) -> bool:
    """Point one binding at the try setup, in place; True when it needs the password."""
    if "base_url" in binding:
        # The fake APIs take no token; a secret here would only be a keychain entry to miss.
        binding.pop("secret", None)
        return False
    if native:
        for key in ("database", "schema"):
            if key in binding:
                binding[key] = NAMES.get(binding[key], binding[key])
        binding["user"] = RO_LOGIN
    binding["secret"] = f"keyring:{TRY_SERVICE}/{connector}.{env}"
    return True


def not_local(root: Path) -> list[str]:
    """Every binding under root that could reach anything but this machine."""
    found = []
    for path in sorted(root.rglob("connectors/*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for env, binding in document["environments"].items():
            where = binding.get("base_url") or binding.get("host")
            local = _LOCAL_URL.match(str(where)) if "base_url" in binding else where == "127.0.0.1"
            if not local:
                found.append(f"{document['connector']} in {env}: {where} is not on 127.0.0.1")
    return found


def make_try_config(examples: Path, out: Path, native: bool, password: str) -> list[str]:
    """Write the copy and save the password for each SQL binding; the keychain names used."""
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(examples, out)
    saved = []
    for path in sorted(out.rglob("connectors/*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        name = document["connector"]
        for env, binding in document["environments"].items():
            if try_binding(name, env, binding, native):
                saved.append(f"{name}.{env}")
        text = "# A copy of examples/ made by dev/try.sh.\n" + yaml.safe_dump(
            document, sort_keys=False
        )
        path.write_text(text, encoding="utf-8", newline="\n")
    problems = not_local(out)
    if problems:
        shutil.rmtree(out)
        sys.exit("refusing this config:\n  " + "\n  ".join(problems))
    for username in saved:
        save_secret(TRY_SERVICE, username, password)
    return [f"{TRY_SERVICE}/{username}" for username in saved]


def main() -> None:
    """Write the copy for docker or native."""
    match sys.argv[1:]:
        case [examples, out, ("docker" | "native") as setup]:
            password = os.environ.get("TRY_RO_PASSWORD")
            if not password:
                sys.exit("TRY_RO_PASSWORD is not set: run this through dev/try.sh")
            saved = make_try_config(Path(examples), Path(out), setup == "native", password)
            print(f"Passwords saved in your keychain as {', '.join(saved)}")
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main()
