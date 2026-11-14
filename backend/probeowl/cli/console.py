# Author: @SaadZarook
"""Whether owl's console takes colour: one rule for owl's own output and owl serve's log.

Colour only when the console can show it: NO_COLOR unset (https://no-color.org), the output a
terminal, not TERM=dumb, and on Windows the console's virtual terminal processing turned on.
Otherwise, plain text: an administrator PowerShell without it shows colour codes as ←[32m.
"""

import os
import sys
from collections.abc import Callable, Mapping
from typing import TextIO

NO_COLOR = "NO_COLOR"
_STD_OUTPUT_HANDLE = -11
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


def colour_supported(
    stream: TextIO | None = None,
    environ: Mapping[str, str] = os.environ,
    windows: bool = os.name == "nt",
    turn_on_vt: Callable[[], bool] | None = None,
) -> bool:
    """Whether to write colour to stream (standard output unless given)."""
    if environ.get(NO_COLOR, "") != "":
        return False
    stream = stream if stream is not None else sys.stdout
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):  # a replaced or closed stream: no terminal to colour
        return False
    if environ.get("TERM") == "dumb":
        return False
    if windows:
        return (turn_on_vt or _turn_on_virtual_terminal)()
    return True


def plain_unless_coloured(environ: dict[str, str] | None = None) -> None:
    """Set NO_COLOR for this process when colour isn't supported, so every library honours it."""
    target = environ if environ is not None else os.environ
    if not colour_supported(environ=target):
        target[NO_COLOR] = "1"


def _turn_on_virtual_terminal() -> bool:
    """Ask the Windows console to interpret colour codes; whether it now does."""
    import ctypes  # Windows only: imported where it's used

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    kernel32 = windll.kernel32
    handle = kernel32.GetStdHandle(_STD_OUTPUT_HANDLE)
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False  # not a console: redirected, or a terminal without the API
    if mode.value & _ENABLE_VIRTUAL_TERMINAL_PROCESSING:
        return True
    return bool(kernel32.SetConsoleMode(handle, mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING))
