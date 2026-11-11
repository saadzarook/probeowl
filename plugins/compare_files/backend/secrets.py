# Author: @SaadZarook
"""Which values look like secrets, and where they sit in the file they came from.

Keys named like a secret and connection strings, as the design says. Masking too much is a
nuisance and masking too little is a leak, so a key only has to contain one of these words.
"""

import json
import re
from collections.abc import Iterable, Iterator, Mapping

from probeowl.plugins.api import MASKED, Leaf

MIN_SHARED_SECRET = 8

_SECRET_KEY = re.compile(
    r"pass(word|wd|phrase)?|pwd|token|secret|api_?key|private_?key|credential|bearer|session_?key",
    re.IGNORECASE,
)
# scheme://user:password@host, or Password=... inside a connection string.
_CONNECTION_STRING = re.compile(
    r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@|(^|;)\s*(password|pwd)\s*=", re.IGNORECASE
)
# A whole line of plain text has no key: this is how one looks in a line.
_SECRET_IN_LINE = re.compile(
    r"(pass(word|wd|phrase)?|pwd|token|secret|api_?key|private_?key|credential)\w*[\"']?\s*[:=]",
    re.IGNORECASE,
)


def looks_secret(key: str | None, value: object) -> bool:
    """Whether a value under this key should be masked."""
    if key is not None and _SECRET_KEY.search(key):
        return True
    return isinstance(value, str) and _CONNECTION_STRING.search(value) is not None


def masker(leaf: Leaf) -> bool:
    """For the comparison engine."""
    return looks_secret(leaf.key, leaf.value)


def line_masker(leaf: Leaf) -> bool:
    """For plain text, where a value is a whole line."""
    return looks_secret_line(str(leaf.value))


def looks_secret_line(line: str) -> bool:
    """Whether a line of plain text should be masked."""
    return looks_secret(None, line) or _SECRET_IN_LINE.search(line) is not None


def secrets_in(tree: object, key: str | None = None) -> Iterator[object]:
    """Every secret in a tree. Its own walk: the engine does not visit ignored paths or matched
    array elements, and the side-by-side view shows those too.
    """
    if isinstance(tree, Mapping):
        for name, value in tree.items():
            yield from secrets_in(value, str(name))
    elif isinstance(tree, list):
        for value in tree:
            yield from secrets_in(value, key)
    elif looks_secret(key, tree):
        yield tree


def as_written(value: object) -> str:
    """How a parsed value most likely appears in the file."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def find_exactly(text: str, value: object) -> tuple[int, int] | None:
    """Where the value first appears exactly, as code points; None if it does not."""
    written = as_written(value)
    start = text.find(written) if written else -1
    return (start, start + len(written)) if start >= 0 else None


def shared_secrets(values: Iterable[object]) -> set[str]:
    """The secrets to hide wherever their exact text appears, in either file.

    Only long ones: a password of "a" or "true" must not hide every other a or true there.
    """
    return {w for v in values if len(w := as_written(v)) >= MIN_SHARED_SECRET}


def contains_shared(value: object, shared: set[str]) -> bool:
    """Whether a value holds a secret found anywhere in either file."""
    written = as_written(value)
    return any(secret in written for secret in shared)


def without_shared(text: str, shared: set[str]) -> str:
    """The text with each secret replaced: for what the masker never sees, such as a path."""
    for secret in shared:
        text = text.replace(secret, MASKED)
    return text


def _appearances(text: str, written: str) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for form in {written, json.dumps(written)[1:-1], written.replace('"', '""')}:
        start = text.find(form)
        while start >= 0:
            found.append((start, start + len(form)))
            start = text.find(form, start + 1)
    return found


def ranges_of(
    text: str, values: Iterable[object], shared: Iterable[str] = ()
) -> list[tuple[int, int]] | None:
    """Everywhere these values appear, merged; None if any of them cannot be located.

    Encoded forms count here, not for Reveal: to hide `a\\nb` the escaped text is what must go.
    shared: secrets from either file, which this one need not contain.
    """
    found: list[tuple[int, int]] = []
    for value in values:
        written = as_written(value)
        if not written:
            continue
        appearances = _appearances(text, written)
        if not appearances:
            return None
        found += appearances
    for secret in shared:
        found += _appearances(text, secret)
    found.sort()
    merged: list[tuple[int, int]] = []
    for start, end in found:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


class Offsets:
    """Python counts code points; JavaScript strings count UTF-16 units, so an emoji is two."""

    def __init__(self, text: str):
        self._astral = [i for i, c in enumerate(text) if ord(c) > 0xFFFF]

    def __call__(self, offset: int) -> int:
        low, high = 0, len(self._astral)
        while low < high:
            middle = (low + high) // 2
            if self._astral[middle] < offset:
                low = middle + 1
            else:
                high = middle
        return offset + low
