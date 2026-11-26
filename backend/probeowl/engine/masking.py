# Author: @SaadZarook
"""Masking in the runner: PII never leaves it, so evidence, the store and the API never see it."""

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from pydantic import JsonValue

from probeowl.model.common import split_field_ref
from probeowl.model.entity import Entity, LocationBase

MASKED = "[masked]"


def pii_fields(entity: Entity) -> set[str]:
    """Every field that holds a PII identifier, lowercase: its name, and the field its via names.

    A PII identifier is masked wherever it shows, whatever each location's own pii list says:
    as a run's subject, in a match, in a location's rows and in a lookup's parameters (#66).
    """
    fields: set[str] = set()
    for name, identifier in entity.identifiers.items():
        if identifier.pii:
            fields.add(name.lower())
            if identifier.via is not None:
                fields.add(split_field_ref(identifier.via)[1].lower())
    return fields


def key_is_pii(entity: Entity) -> bool:
    """Whether the root key is PII, so a run's subject is stored and shown masked."""
    return entity.identifiers[entity.root_key].pii


def masked_key_reason(entity: Entity) -> str:
    """Why a value a run stored masked must be pasted, as every place that asks says it."""
    return f"{entity.entity}'s key is PII and stored masked: paste its {entity.root_key}"


@dataclass(frozen=True)
class Masked:
    """The fields a location's rows mask. `field in masked` answers for a name or a dotted path.

    fields: names and dotted paths, lowercase. shown: for a REST location, the fields it exposes,
    lowercase; every other field of its JSON is masked. None for SQL, which selects its columns.
    """

    fields: frozenset[str] = frozenset()
    shown: frozenset[str] | None = None

    def __contains__(self, field: object) -> bool:
        if not isinstance(field, str):
            return False
        path = field.lower().split(".")
        if self.shown is not None and path[0] not in self.shown:
            return True
        # A masked field masks everything inside it: mask customer, and customer.email is masked.
        return any(".".join(path[:end]) in self.fields for end in range(1, len(path) + 1))


NOTHING_MASKED = Masked()


def is_rest(location: LocationBase) -> bool:
    """Whether the location reads a REST API: its lookup is a GET, where SQL is only a SELECT."""
    return location.lookup.lstrip()[:4].upper() == "GET "


def masked_fields(entity: Entity, location: LocationBase) -> Masked:
    """The fields masked in this location's rows: its own pii and mask, and every PII identifier.

    A REST location also masks every field it does not expose. An API answers with whatever it
    has, so a field is shown only when the entity says it may be.
    """
    fields = frozenset({f.lower() for f in [*location.pii, *location.mask]} | pii_fields(entity))
    shown = frozenset(f.lower() for f in location.exposes) if is_rest(location) else None
    return Masked(fields, shown)


def mask_rows(rows: list[dict[str, Any]], fields: Collection[str] | Masked) -> list[dict[str, Any]]:
    """Copies of rows with each field masked: SQL column names or dotted REST paths."""
    named = fields.fields if isinstance(fields, Masked) else fields
    shown = fields.shown if isinstance(fields, Masked) else None
    paths = [field.lower().split(".") for field in named]
    return [_unexposed(_mask(row, paths), shown) for row in rows]


def _unexposed(row: Any, shown: frozenset[str] | None) -> Any:
    if shown is None or not isinstance(row, dict):
        return row
    # The whole value, an object or a list too: its name shows, so a person can choose to expose it.
    return {
        key: item if key.lower() in shown or item is None else MASKED for key, item in row.items()
    }


def _mask(value: Any, paths: list[list[str]]) -> Any:
    if isinstance(value, list):
        return [_mask(item, paths) for item in value]
    if not isinstance(value, dict):
        return value
    masked: dict[str, Any] = {}
    for key, item in value.items():
        # Column names keep their database's case; a pii list is matched without it.
        here = [path[1:] for path in paths if path[0] == key.lower()]
        if any(not rest for rest in here):
            # A null stays null: it shows a missing value without revealing a present one.
            masked[key] = None if item is None else MASKED
        else:
            masked[key] = _mask(item, here) if here else item
    return masked


# Shorter than this, a masked value is only matched whole: "42" would otherwise hide half the text.
_LEAST_WITHIN = 4


def mask_values(value: Any, hidden: Collection[str]) -> Any:
    """A copy of value with each hidden value masked, whatever field it is in, keys too.

    For what a lookup by a masked key answers: a table may hold the key in a column of another
    name, and an API may echo it under any name, or in a link.
    """
    if not hidden:
        return value
    if isinstance(value, dict):
        return {k: mask_values(v, hidden) for k, v in mask_keys(value, hidden).items()}
    if isinstance(value, list):
        return [mask_values(item, hidden) for item in value]
    if isinstance(value, str):
        return mask_text(value, hidden)
    if value is None or isinstance(value, bool):
        return value
    return MASKED if str(value) in hidden else value


def mask_keys(value: dict[str, Any], hidden: Collection[str]) -> dict[str, Any]:
    """A copy with each key that is a hidden value masked; part of one may be a field's name."""
    whole = {v.casefold() for v in hidden}
    return {MASKED if str(k).casefold() in whole else k: v for k, v in value.items()}


def mask_text(text: str, hidden: Collection[str]) -> str:
    """text with each hidden value masked: as it is, or URL-encoded."""
    if any(text.casefold() == value.casefold() for value in hidden):
        return MASKED
    within = {v for v in hidden if len(v) >= _LEAST_WITHIN}
    within |= {quote(v, safe="") for v in within}
    if not within:
        return text
    # Longest first, so a value is not left half masked by a shorter one inside it.
    found = "|".join(re.escape(v) for v in sorted(within, key=len, reverse=True))
    return re.sub(found, MASKED, text, flags=re.IGNORECASE)


def to_json(value: Any) -> JsonValue:
    """A value as JSON for evidence: decimals and dates as exact text."""
    if isinstance(value, dict):
        return {str(key): to_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
