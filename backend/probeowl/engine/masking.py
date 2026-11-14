# Author: @SaadZarook
"""Masking in the runner: PII never leaves it, so evidence, the store and the API never see it."""

from collections.abc import Collection
from datetime import date, datetime
from decimal import Decimal
from typing import Any

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


def masked_fields(entity: Entity, location: LocationBase) -> set[str]:
    """The fields masked in this location's rows: its own pii and mask, and every PII identifier."""
    return {f.lower() for f in [*location.pii, *location.mask]} | pii_fields(entity)


def mask_rows(rows: list[dict[str, Any]], fields: Collection[str]) -> list[dict[str, Any]]:
    """Copies of rows with each field masked: SQL column names or dotted REST paths."""
    paths = [field.lower().split(".") for field in fields]
    return [_mask(row, paths) for row in rows]


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
