# Author: @SaadZarook
"""owl's entry point: `owl serve` says it is starting before the heavy imports.

The first start after an install can take about 20 seconds on Windows while antivirus checks the
new files, nearly all of it in imports. This module imports nothing of ProbeOwl's until it has
printed, so a slow first start never looks hung.
"""

import sys

STARTING = "Starting ProbeOwl…"


def starting_line(argv: list[str]) -> str | None:
    """The line to print first, for `owl serve` only, and not for its --help."""
    if argv[:1] != ["serve"] or "--help" in argv:
        return None
    return STARTING


def main() -> None:
    """Say so for `owl serve`, then run owl."""
    line = starting_line(sys.argv[1:])
    if line is not None:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:  # a legacy code page with no "…"
            print(line.replace("…", "..."), flush=True)
    from probeowl.cli.main import main as run_owl

    run_owl()
