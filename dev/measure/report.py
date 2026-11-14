# Author: @SaadZarook
"""The newcomer path's measure as a Markdown table, from the parts its script records.

    report.py <work folder> <probeowl version> [baseline | first-run]

baseline (newcomer_baseline.sh, v0.1): install.json, init.json, serve.json and browser.json.
first-run (newcomer_first_run.sh, v0.2): install.json, serve.json and browser.json; there is no
owl init, and everything after owl serve happens in the browser.
"""

import json
import sys
from pathlib import Path
from typing import Any

# What the guide tells a newcomer to type (guide/docs/getting-started.md, first-application.md).
COMMANDS = {
    "baseline": [
        "uv tool install probeowl",
        "uv tool update-shell",
        "owl --version",
        "owl init",
        "owl serve --config probeowl",
    ],
    # From v0.2, owl serve with no folder named opens first-run setup in the browser, and its first
    # line shows the version, so the guide has no separate owl --version.
    "first-run": [
        "uv tool install probeowl",
        "uv tool update-shell",
        "owl serve",
    ],
}
INSTALL = {
    "baseline": "`uv tool install`, cold cache, from PyPI",
    "first-run": "`uv tool install` of the wheel, cold cache, its dependencies from PyPI",
}
NOTES = {
    "baseline": [
        "An action is a command run, an answer to `owl init` (Enter for a default counts),",
        "a click, a choice in a select, a field filled, or a key that is not a character.",
        "Characters leave out the read-only password, typed once. Machine seconds exclude",
        "a person's reading and thinking time.",
    ],
    "first-run": [
        "An action is a command run, a click, a choice in a select, a field filled, or a key",
        "that is not a character. Characters leave out the read-only password, typed once.",
        "The connector id is left as suggested and not counted: the run replaces it with a",
        "test-only id for its keychain entry, deleted after. Machine seconds exclude a person's",
        "reading and thinking time.",
    ],
}


def read(work: Path, part: str) -> dict[str, Any]:
    """One part the script recorded."""
    data: dict[str, Any] = json.loads((work / f"{part}.json").read_text(encoding="utf-8"))
    return data


def main() -> int:
    """Print the table."""
    work, version = Path(sys.argv[1]), sys.argv[2]
    path = sys.argv[3] if len(sys.argv) > 3 else "baseline"
    if path not in COMMANDS:
        sys.exit(f"the path is baseline or first-run, not {path}")
    commands = COMMANDS[path]
    install, serve = read(work, "install"), read(work, "serve")
    browser = json.loads((work / "browser.json").read_text(encoding="utf-8"))

    rows = [
        ("Commands typed", len(commands), sum(map(len, commands)), None, "the guide's commands"),
        ("Install", 0, 0, install["seconds"], INSTALL[path]),
    ]
    if (work / "settle.json").exists():
        settle = read(work, "settle")["seconds"]
        note = "three imports of what owl serve loads, while antivirus checks the new files"
        rows.append(("First loads after install", 0, 0, settle, note))
    if path == "baseline":
        init = read(work, "init")
        note = f"{init['answers']} answers, {init['defaults']} by Enter; Test and import on"
        rows.append(("owl init", init["answers"], init["characters"], init["seconds"], note))
    rows.append(("owl serve", 0, 0, serve["seconds"], "until the API answers"))
    rows += [(s["name"], s["actions"], s["characters"], s["seconds"], "browser") for s in browser]

    title = "install to first Validate"
    if path == "first-run":
        title = "install, then owl serve in an empty folder, to first Validate"
    lines = [
        f"## Newcomer path on ProbeOwl {version}: {title}",
        "",
        "| Segment | Actions | Characters | Machine seconds | Notes |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for name, actions, characters, seconds, note in rows:
        shown = "" if seconds is None else f"{seconds:.1f}"
        lines.append(f"| {name} | {actions} | {characters} | {shown} | {note} |")
    actions = sum(r[1] for r in rows)
    characters = sum(r[2] for r in rows)
    seconds = sum(r[3] for r in rows if r[3] is not None)
    lines.append(f"| **Total** | **{actions}** | **{characters}** | **{seconds:.1f}** | |")
    lines += ["", *NOTES[path]]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
