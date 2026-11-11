# Author: @SaadZarook
"""Masking in the runner: PII never leaves it, so evidence, the store and the API never see it."""

from collections.abc import Collection
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import JsonValue

MASKED = "[masked]"


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
