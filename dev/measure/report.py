# Author: @SaadZarook
"""The newcomer path's measure as a Markdown table, from the parts newcomer_baseline.sh records.

    report.py <work folder> <probeowl version>

Reads install.json, init.json, serve.json and browser.json from the work folder.
"""

import json
import sys
from pathlib import Path

# What the guide tells a newcomer to type (guide/docs/getting-started.md, first-application.md).
COMMANDS = [
    "uv tool install probeowl",
    "uv tool update-shell",
    "owl --version",
    "owl init",
    "owl serve --config probeowl",
]


def main() -> int:
    work, version = Path(sys.argv[1]), sys.argv[2]
    install, init, serve, browser = (
        json.loads((work / f"{part}.json").read_text(encoding="utf-8"))
        for part in ("install", "init", "serve", "browser")
    )

    rows = [
        ("Commands typed", len(COMMANDS), sum(map(len, COMMANDS)), None, "the guide's commands"),
        ("Install", 0, 0, install["seconds"], "`uv tool install`, cold cache, from PyPI"),
        (
            "owl init",
            init["answers"],
            init["characters"],
            init["seconds"],
            f"{init['answers']} answers, {init['defaults']} by Enter; Test and import on",
        ),
        ("owl serve", 0, 0, serve["seconds"], "until the API answers"),
    ] + [(s["name"], s["actions"], s["characters"], s["seconds"], "browser") for s in browser]

    lines = [
        f"## Newcomer path on ProbeOwl {version}: install to first Validate",
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
    lines += [
        "",
        "An action is a command run, an answer to `owl init` (Enter for a default counts),",
        "a click, a choice in a select, a field filled, or a key that is not a character.",
        "Characters leave out the read-only password, typed once. Machine seconds exclude",
        "a person's reading and thinking time.",
    ]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
