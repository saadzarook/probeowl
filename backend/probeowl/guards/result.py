# Author: @SaadZarook
"""The result guard: a second cap on rows and bytes, behind the SQL row cap."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CappedResult:
    """Rows kept within the caps, and why any were left out."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    truncated: str | None = None


def cap_result(rows: Iterable[Mapping[str, Any]], max_rows: int, max_bytes: int) -> CappedResult:
    """Keep rows until max_rows or max_bytes is reached, reading no further than needed."""
    kept: list[dict[str, Any]] = []
    size = 0
    for row in rows:
        if len(kept) == max_rows:
            return CappedResult(kept, f"more than {max_rows} rows")
        # default=str: dates and decimals count at their printed size.
        size += len(json.dumps(row, default=str).encode())
        if size > max_bytes:
            return CappedResult(kept, f"more than {max_bytes} bytes")
        kept.append(dict(row))
    return CappedResult(kept)
