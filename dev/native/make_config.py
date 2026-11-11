# Author: @SaadZarook
"""Copy examples/ to a folder whose bindings point only at the native setup's test databases.

    make_config.py <examples> <out>          the copy the exit checks serve
    make_config.py <examples> <out> --bad    m3's second copy, as its CI job makes it

examples/ itself is never changed. Secrets become environment variables, so a run never touches
the keychain entries a real connector would use. It refuses to finish on any binding that is not
on 127.0.0.1, or whose database, schema or login does not contain "test".
"""

import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

SECRET = "env:PROBEOWL_NATIVE_RO_PASSWORD"
RO_LOGIN = "probeowl_test_ro"
# Each example binding's database or schema, and its copy in the native setup.
NAMES = {
    "wallet": "wallet_test",
    "wallet_uat": "wallet_uat_test",
    "access": "access_test",
    "access_uat": "access_uat_test",
    "LEDGER": "LEDGER_TEST",
    "LEDGER_UAT": "LEDGER_UAT_TEST",
}
_LOCAL_URL = re.compile(r"^https?://127\.0\.0\.1(:\d+)?(/|$)")


def rewrite_connector(text: str) -> str:
    """A connector's YAML with test names and a secret from the environment, comments kept."""
    for key in ("database", "schema"):
        text = re.sub(
            rf"^(\s*{key}: )(\w+)(\s*)$",
            lambda m: f"{m.group(1)}{NAMES.get(m.group(2), m.group(2))}{m.group(3)}",
            text,
            flags=re.M,
        )
    text = re.sub(r"^(\s*user: )probeowl_ro(\s*)$", rf"\g<1>{RO_LOGIN}\g<2>", text, flags=re.M)
    return re.sub(r"^(\s*secret: )\S+(\s*)$", rf"\g<1>{SECRET}\g<2>", text, flags=re.M)


def spoil_for_m3(root: Path) -> None:
    """What m3's job does to its second copy: an unsaveable lookup, and UAT bindings that fail."""
    entity = root / "wallet" / "entities" / "topup.yaml"
    before = entity.read_text(encoding="utf-8")
    after = before.replace("WHERE txn_id = :ledger_txn_id", "WHERE account = :ledger_txn_id")
    _changed(entity, before, after)
    _spoil_uat(root / "wallet" / "connectors" / "ledger_db.yaml", "port: 1521", "port: 1")
    _spoil_uat(
        root / "card-access" / "connectors" / "access_db.yaml",
        f"user: {RO_LOGIN}",
        "user: probeowl_test_nobody",
    )


def _spoil_uat(path: Path, old: str, new: str) -> None:
    before = path.read_text(encoding="utf-8")
    head, uat, tail = before.partition("\n  uat:")
    _changed(path, before, head + uat + tail.replace(old, new))


def _changed(path: Path, before: str, after: str) -> None:
    if before == after:
        sys.exit(f"{path} no longer has what m3's bad copy changes")
    path.write_text(after, encoding="utf-8", newline="\n")


def unsafe_bindings(root: Path) -> list[str]:
    """Every binding under root that could reach anything but the native test databases."""
    found: list[str] = []
    for path in sorted(root.rglob("connectors/*.yaml")):
        connector: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        for environment, binding in connector.get("environments", {}).items():
            where = f"{connector.get('connector')} in {environment}"
            found += [f"{where}: {problem}" for problem in _problems(binding)]
    return found


def _problems(binding: dict[str, Any]) -> list[str]:
    if "base_url" in binding:
        # The fake APIs. A REST binding holds no data of ours, but must stay on this machine.
        ok = _LOCAL_URL.match(str(binding["base_url"]))
        return [] if ok else [f"{binding['base_url']} is not on 127.0.0.1"]
    problems = []
    if binding.get("host") != "127.0.0.1":
        problems.append(f"host {binding.get('host')} is not 127.0.0.1")
    named = binding.get("schema") or binding.get("database") or ""
    for what, name in (("database or schema", named), ("login", binding.get("user", ""))):
        if "test" not in str(name).lower():
            problems.append(f"{what} {name} does not contain 'test'")
    if not str(binding.get("secret", "")).startswith("env:PROBEOWL_NATIVE_"):
        problems.append("its secret is not the native setup's")
    return problems


def make_config(examples: Path, out: Path, bad: bool = False) -> None:
    """Write the copy, or exit if anything in it is not safe to run against."""
    shutil.copytree(examples, out)
    for path in out.rglob("connectors/*.yaml"):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        path.write_text(rewrite_connector(text), encoding="utf-8", newline="\n")
    if bad:
        spoil_for_m3(out)
    problems = unsafe_bindings(out)
    if problems:
        sys.exit("refusing this config:\n  " + "\n  ".join(problems))


def main() -> None:
    """Write one copy."""
    match sys.argv[1:]:
        case [examples, out]:
            make_config(Path(examples), Path(out))
        case [examples, out, "--bad"]:
            make_config(Path(examples), Path(out), bad=True)
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main()
