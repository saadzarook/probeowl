# Author: @SaadZarook
"""What type a value read from a database has, so it can be bound as that type again.

Evidence keeps dates and decimals as exact text. Bound as text, a date is converted using the
server's language and date format, which can silently flip day and month.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from pydantic import JsonValue

from probeowl.guards.statement import ParameterValue
from probeowl.model.run import ValueKind


def kind_of(value: Any) -> ValueKind:
    """The kind of one value a driver returned. bool and datetime first: each is also its base."""
    if isinstance(value, bool):
        return ValueKind.BOOLEAN
    if isinstance(value, int):
        return ValueKind.INTEGER
    if isinstance(value, float):
        return ValueKind.FLOAT
    if isinstance(value, Decimal):
        return ValueKind.DECIMAL
    if isinstance(value, datetime):
        return ValueKind.DATETIME
    if isinstance(value, date):
        return ValueKind.DATE
    if isinstance(value, time):
        return ValueKind.TIME
    return ValueKind.TEXT


def column_kinds(rows: Iterable[Mapping[str, Any]]) -> dict[str, ValueKind]:
    """Each column's kind, from its first value that is not null. Nested values have none."""
    kinds: dict[str, ValueKind] = {}
    for row in rows:
        for column, value in row.items():
            if column not in kinds and value is not None and not isinstance(value, dict | list):
                kinds[column] = kind_of(value)
    return kinds


def restore(value: JsonValue, kind: ValueKind) -> ParameterValue:
    """A value as evidence kept it, back as its real type. ValueError if it is not one."""
    if not isinstance(value, str | int | float | bool):
        raise ValueError(f"{value!r} is not a single value")
    match kind:
        case ValueKind.DATETIME:
            return datetime.fromisoformat(str(value))
        case ValueKind.DATE:
            return date.fromisoformat(str(value))
        case ValueKind.TIME:
            return time.fromisoformat(str(value))
        case ValueKind.DECIMAL:
            return Decimal(str(value))
        case ValueKind.INTEGER:
            return int(value)
        case ValueKind.FLOAT:
            return float(value)
        case ValueKind.BOOLEAN:
            return bool(value)
        case ValueKind.TEXT:
            return str(value)
