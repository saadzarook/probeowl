# Author: @SaadZarook
from collections.abc import Iterator
from datetime import date
from typing import Any

from probeowl.guards.result import cap_result


def rows(count: int) -> Iterator[dict[str, Any]]:
    for i in range(count):
        yield {"id": i, "posted_at": date(2026, 10, 31)}


def test_rows_within_the_caps_are_all_kept() -> None:
    result = cap_result(rows(3), max_rows=10, max_bytes=10_000)
    assert len(result.rows) == 3
    assert result.truncated is None


def test_rows_beyond_the_row_cap_are_left_out_and_reported() -> None:
    result = cap_result(rows(50), max_rows=10, max_bytes=10_000)
    assert len(result.rows) == 10
    assert result.truncated == "more than 10 rows"


def test_rows_beyond_the_byte_cap_are_left_out_and_reported() -> None:
    big = ({"payload": "x" * 400} for _ in range(10))
    result = cap_result(big, max_rows=100, max_bytes=1_000)
    assert len(result.rows) == 2
    assert result.truncated == "more than 1000 bytes"


def test_exactly_max_rows_is_not_truncated() -> None:
    assert cap_result(rows(10), max_rows=10, max_bytes=10_000).truncated is None


def test_reading_stops_at_the_cap() -> None:
    read = 0

    def counting() -> Iterator[dict[str, Any]]:
        nonlocal read
        for row in rows(1_000_000):
            read += 1
            yield row

    cap_result(counting(), max_rows=5, max_bytes=10_000)
    assert read == 6  # the sixth row shows there are more; nothing after it is read
