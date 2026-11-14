# Author: @SaadZarook
"""The credential scan of a draft (#39): what looks like a secret, a host or an address.

Run on every draft before any check. What it finds is reported by kind and line, with only its
length shown, never any part of it. The values are kept just long enough to remove them from
the checks' own messages, and are never returned, logged or audited.
"""

import math
import re
from dataclasses import dataclass
from enum import StrEnum

# Placeholders a template uses; values like these are not credentials.
PLACEHOLDER = re.compile(r"^<[^<>]+>$")
REFERENCES = ("keyring:", "env:")


class CredentialKind(StrEnum):
    """What a found value looks like."""

    PASSWORD = "password"
    TOKEN = "token"
    CONNECTION_STRING = "connection_string"
    PRIVATE_KEY = "private_key"
    HOST = "host"
    IP_ADDRESS = "ip_address"


@dataclass(frozen=True)
class Found:
    """One value the scan found, on a line from 1."""

    kind: CredentialKind
    line: int
    value: str

    def shown(self) -> str:
        """The kind and length only: no character of the value."""
        return f"{self.kind.value} ({len(self.value)} characters)"


_KEY_VALUE = re.compile(r"""^\s*-?\s*["']?([A-Za-z_][A-Za-z0-9_-]*)["']?\s*:\s*(.+?)\s*$""")
_SECRET_KEYS = {"password", "pwd", "passwd", "secret", "token", "api_key", "apikey"}
_HOST_KEYS = {"host", "hostname", "server", "base_url", "url"}
_URL_CREDENTIALS = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)
_PASSWORD_PAIR = re.compile(r"(?i)\b(?:password|pwd)\s*=\s*([^;\s'\"]+)")
_BEARER = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{8,})")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_IPV4 = re.compile(
    r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
)
_IPV6 = re.compile(
    r"(?<![:\w])(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}(?![:\w])|(?<![:\w])::1(?![:\w])"
)
_LONG_RANDOM = re.compile(r"[A-Za-z0-9+/=_-]{32,}")


def scan(text: str) -> list[Found]:
    """Every credential-looking value in the draft, line by line."""
    found: list[Found] = []
    for number, line in enumerate(text.splitlines(), start=1):
        found.extend(_on_line(line, number))
    return found


def redacted(message: str, found: list[Found]) -> str:
    """A check's message with every found value replaced, longest first."""
    for each in sorted(found, key=lambda f: len(f.value), reverse=True):
        message = message.replace(each.value, f"[{each.kind.value}]")
    return message


def _on_line(line: str, number: int) -> list[Found]:
    found: list[Found] = []
    if _PRIVATE_KEY.search(line):
        found.append(Found(CredentialKind.PRIVATE_KEY, number, line.strip()))
    for match in _URL_CREDENTIALS.finditer(line):
        found.append(Found(CredentialKind.CONNECTION_STRING, number, match.group(0)))
    for match in _PASSWORD_PAIR.finditer(line):
        found.append(Found(CredentialKind.CONNECTION_STRING, number, match.group(1)))
    for pattern in (_BEARER, _JWT, _AWS_KEY):
        for match in pattern.finditer(line):
            value = match.group(1) if pattern is _BEARER else match.group(0)
            found.append(Found(CredentialKind.TOKEN, number, value))
    pair = _KEY_VALUE.match(line)
    if pair:
        key, value = pair.group(1).lower(), _unquoted(pair.group(2))
        if key in _SECRET_KEYS and value and not _is_reference(value):
            found.append(Found(CredentialKind.PASSWORD, number, value))
        elif key in _HOST_KEYS and value and not PLACEHOLDER.match(value):
            found.append(Found(CredentialKind.HOST, number, value))
    for pattern in (_IPV4, _IPV6):
        for match in pattern.finditer(line):
            found.append(Found(CredentialKind.IP_ADDRESS, number, match.group(0)))
    for match in _LONG_RANDOM.finditer(line):
        if _random_looking(match.group(0)) and not any(f.value == match.group(0) for f in found):
            found.append(Found(CredentialKind.TOKEN, number, match.group(0)))
    return _unique(found)


def _unquoted(value: str) -> str:
    value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _is_reference(value: str) -> bool:
    return value.startswith(REFERENCES) or bool(PLACEHOLDER.match(value))


def _random_looking(text: str) -> bool:
    # A long string of mixed characters with high entropy: a key, not a word or an identifier.
    if not (any(c.isdigit() for c in text) and any(c.isalpha() for c in text)):
        return False
    counts = {c: text.count(c) for c in set(text)}
    entropy = -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())
    return entropy > 4.0


def _unique(found: list[Found]) -> list[Found]:
    seen: set[tuple[CredentialKind, str]] = set()
    kept = []
    for each in found:
        if (each.kind, each.value) not in seen:
            seen.add((each.kind, each.value))
            kept.append(each)
    return kept
