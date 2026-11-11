# Author: @SaadZarook
"""The comparison engine: what differs between two values, by path. Pure: no I/O, no connectors.

Shared by every comparison, so they all have one set of rules for paths, ignore lists and masking.
A masked value is compared, but its value never leaves here: a difference says that it differs and
holds the marker in its place, so nothing downstream can show or log it by accident.
"""

import difflib
import fnmatch
import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from probeowl.errors import RequestError

MASKED = "[masked]"
# Past this, matching lines would take minutes on a laptop: they are compared by position instead.
MAX_LINES_TO_MATCH = 20_000


class Change(StrEnum):
    """How a path differs."""

    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"


class ArrayMatch(StrEnum):
    """How array elements are paired."""

    BY_POSITION = "by_position"
    ANY_ORDER = "any_order"
    BY_KEY = "by_key"


class Side(StrEnum):
    """Which of the two inputs."""

    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True)
class Leaf:
    """One value, as a masker sees it."""

    side: Side
    path: str
    key: str | None  # the last key on the path, if the value is under one
    value: object


Masker = Callable[[Leaf], bool]


def _never(leaf: Leaf) -> bool:
    return False


@dataclass(frozen=True)
class Options:
    """What counts as the same."""

    ignore_paths: tuple[str, ...] = ()
    ignore_key_order: bool = True
    arrays: ArrayMatch = ArrayMatch.BY_POSITION
    array_key: str | None = None  # the field to pair by, with ArrayMatch.BY_KEY
    ignore_whitespace: bool = False
    ignore_case: bool = False
    number_tolerance: Decimal = Decimal(0)
    masker: Masker = _never


@dataclass(frozen=True)
class Difference:
    """One path that differs. left and right are scalars, or the marker if masked."""

    path: str
    change: Change
    left: object = None
    right: object = None
    masked: bool = False


@dataclass(frozen=True)
class Comparison:
    """Everything that differs, and how much did not."""

    differences: list[Difference]
    unchanged: int
    ignored: int
    notes: list[str] = field(default_factory=list)

    def count(self, change: Change) -> int:
        """How many differences are of this kind."""
        return sum(d.change is change for d in self.differences)

    @property
    def masked(self) -> int:
        """How many differences hide a value."""
        return sum(d.masked for d in self.differences)


def compare(left: object, right: object, options: Options | None = None) -> Comparison:
    """What differs between two trees of mappings, lists and scalars."""
    walk = _Walk(options or Options())
    try:
        walk.values("", None, left, right)
    except RecursionError:
        raise RequestError("the input is nested too deeply to compare") from None
    return Comparison(walk.differences, walk.unchanged, walk.ignored, walk.notes)


def compare_lines(left: str, right: str, options: Options | None = None) -> Comparison:
    """What differs between two texts, line by line. Paths are `line <n>`, counted per side."""
    walk = _Walk(options or Options())
    walk.lines(left.splitlines(), right.splitlines())
    return Comparison(walk.differences, walk.unchanged, walk.ignored, walk.notes)


def child_path(parent: str, key: object) -> str:
    """The path of a key: payments.retry, or a quoted key if a dot or bracket would mislead."""
    name = str(key)
    if any(c in name for c in '.[]"') or name == "":
        name = json.dumps(name)
        return f"{parent}[{name}]"
    return f"{parent}.{name}" if parent else name


def _ignored(path: str, patterns: tuple[str, ...]) -> bool:
    # Square brackets are array positions in a path, never fnmatch character sets.
    escaped = path.replace("[", "\0").replace("]", "\1")
    for pattern in patterns:
        wanted = pattern.replace("[", "\0").replace("]", "\1")
        if fnmatch.fnmatchcase(escaped, wanted):
            return True
        # *.updated_at means at any depth, the top level included.
        if wanted.startswith("*.") and fnmatch.fnmatchcase(escaped, wanted[2:]):
            return True
    return False


def _is_number(value: object) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


class _Walk:
    def __init__(self, options: Options):
        self.options = options
        self.differences: list[Difference] = []
        self.unchanged = 0
        self.ignored = 0
        self.notes: list[str] = []

    def values(self, path: str, key: str | None, left: object, right: object) -> None:
        if path and _ignored(path, self.options.ignore_paths):
            self.ignored += 1
        elif isinstance(left, Mapping) and isinstance(right, Mapping):
            self._mappings(path, left, right)
        elif isinstance(left, list) and isinstance(right, list):
            self._arrays(path, key, left, right)
        elif isinstance(left, Mapping | list) or isinstance(right, Mapping | list):
            # Different shapes have no pairing, and each leaf of a container may need masking.
            self._one_side(path, key, left, Change.REMOVED)
            self._one_side(path, key, right, Change.ADDED)
        else:
            self._scalars(path, key, left, right)

    def _mappings(
        self, path: str, left: Mapping[object, object], right: Mapping[object, object]
    ) -> None:
        for name, value in left.items():
            child = child_path(path, name)
            if name in right:
                self.values(child, str(name), value, right[name])
            elif _ignored(child, self.options.ignore_paths):
                self.ignored += 1
            else:
                self._one_side(child, str(name), value, Change.REMOVED)
        for name, value in right.items():
            if name not in left:
                child = child_path(path, name)
                if _ignored(child, self.options.ignore_paths):
                    self.ignored += 1
                else:
                    self._one_side(child, str(name), value, Change.ADDED)
        if not self.options.ignore_key_order:
            self._key_order(path, left, right)

    def _key_order(
        self, path: str, left: Mapping[object, object], right: Mapping[object, object]
    ) -> None:
        shared_left = [str(k) for k in left if k in right]
        shared_right = [str(k) for k in right if k in left]
        if shared_left != shared_right:
            self.differences.append(
                Difference(
                    path or "(top level)",
                    Change.CHANGED,
                    f"key order: {', '.join(shared_left)}",
                    f"key order: {', '.join(shared_right)}",
                )
            )

    def _arrays(self, path: str, key: str | None, left: list[object], right: list[object]) -> None:
        if self.options.arrays is ArrayMatch.BY_KEY and self._arrays_by_key(path, left, right):
            return
        rest_left = list(enumerate(left))
        rest_right = list(enumerate(right))
        if self.options.arrays is ArrayMatch.ANY_ORDER:
            rest_left, rest_right = self._without_matches(path, left, right)
        # Each path names the element's real position in its own input.
        for offset in range(max(len(rest_left), len(rest_right))):
            if offset >= len(rest_right):
                index, value = rest_left[offset]
                self._one_side(f"{path}[{index}]", key, value, Change.REMOVED)
            elif offset >= len(rest_left):
                index, value = rest_right[offset]
                self._one_side(f"{path}[{index}]", key, value, Change.ADDED)
            else:
                index, value = rest_left[offset]
                self.values(f"{path}[{index}]", key, value, rest_right[offset][1])

    def _arrays_by_key(self, path: str, left: list[object], right: list[object]) -> bool:
        field_name = self.options.array_key
        left_rows = self._keyed(path, left, Side.LEFT)
        right_rows = self._keyed(path, right, Side.RIGHT)
        if field_name is None or left_rows is None or right_rows is None:
            self.notes.append(
                f"{path or '(top level)'}: matched by position; {field_name} is not a unique, "
                "unmasked key of every element"
            )
            return False
        for value, row in left_rows.items():
            child = f"{path}[{field_name}={value}]"
            if value in right_rows:
                self.values(child, None, row, right_rows[value])
            else:
                self._one_side(child, None, row, Change.REMOVED)
        for value, row in right_rows.items():
            if value not in left_rows:
                self._one_side(f"{path}[{field_name}={value}]", None, row, Change.ADDED)
        return True

    def _keyed(self, path: str, rows: list[object], side: Side) -> dict[str, object] | None:
        field_name = self.options.array_key
        keyed: dict[str, object] = {}
        for row in rows:
            if not isinstance(row, Mapping) or field_name not in row:
                return None
            value = row[field_name]
            leaf = Leaf(side, child_path(path, field_name), field_name, value)
            # The key's value would appear in the path, so a masked one cannot be the key.
            if (
                isinstance(value, Mapping | list)
                or self.options.masker(leaf)
                or str(value) in keyed
            ):
                return None
            keyed[str(value)] = row
        return keyed

    def _without_matches(
        self, path: str, left: list[object], right: list[object]
    ) -> tuple[list[tuple[int, object]], list[tuple[int, object]]]:
        # By an exact form, so it stays linear on large arrays. Whatever is left is paired in
        # order, which is where a tolerance or a real difference shows.
        waiting: dict[str, list[int]] = {}
        for index, value in enumerate(right):
            waiting.setdefault(self._canonical(f"{path}[]", value), []).append(index)
        kept_left: list[tuple[int, object]] = []
        matched: set[int] = set()
        for index, value in enumerate(left):
            found = waiting.get(self._canonical(f"{path}[]", value))
            if found:
                matched.add(found.pop(0))
                self.unchanged += sum(1 for _ in self._leaves(value))
            else:
                kept_left.append((index, value))
        return kept_left, [(i, v) for i, v in enumerate(right) if i not in matched]

    def _canonical(self, path: str, value: object) -> str:
        if isinstance(value, Mapping):
            parts = {
                str(k): self._canonical(child_path(path, k), v)
                for k, v in value.items()
                if not _ignored(child_path(path, k), self.options.ignore_paths)
            }
            return json.dumps(parts, sort_keys=True)
        if isinstance(value, list):
            return json.dumps([self._canonical(f"{path}[]", v) for v in value])
        return f"{type(value).__name__}:{self._normal(value)}"

    def _leaves(self, value: object) -> Iterator[object]:
        if isinstance(value, Mapping):
            for item in value.values():
                yield from self._leaves(item)
        elif isinstance(value, list):
            for item in value:
                yield from self._leaves(item)
        else:
            yield value

    def _one_side(self, path: str, key: str | None, value: object, change: Change) -> None:
        if isinstance(value, Mapping) and value:
            for name, item in value.items():
                child = child_path(path, name)
                if _ignored(child, self.options.ignore_paths):
                    self.ignored += 1
                else:
                    self._one_side(child, str(name), item, change)
        elif isinstance(value, list) and value:
            for index, item in enumerate(value):
                self._one_side(f"{path}[{index}]", key, item, change)
        else:
            side = Side.LEFT if change is Change.REMOVED else Side.RIGHT
            shown, masked = self._shown(side, path, key, value)
            left, right = (shown, None) if change is Change.REMOVED else (None, shown)
            self.differences.append(Difference(path, change, left, right, masked))

    def _shown(self, side: Side, path: str, key: str | None, value: object) -> tuple[object, bool]:
        if self.options.masker(Leaf(side, path, key, value)):
            return MASKED, True
        return value, False

    def _scalars(self, path: str, key: str | None, left: object, right: object) -> None:
        # Either side asking masks both: seeing one side of a secret's change gives half of it away.
        masked_left = self.options.masker(Leaf(Side.LEFT, path, key, left))
        masked_right = self.options.masker(Leaf(Side.RIGHT, path, key, right))
        if self._same(left, right):
            self.unchanged += 1
            return
        if masked_left or masked_right:
            self.differences.append(Difference(path, Change.CHANGED, MASKED, MASKED, True))
        else:
            self.differences.append(Difference(path, Change.CHANGED, left, right))

    def _same(self, left: object, right: object) -> bool:
        if _is_number(left) and _is_number(right):
            return self._same_number(left, right)
        if type(left) is not type(right) and not (isinstance(left, str) and isinstance(right, str)):
            return False
        return self._normal(left) == self._normal(right)

    def _same_number(self, left: object, right: object) -> bool:
        try:
            a, b = Decimal(str(left)), Decimal(str(right))
            if not (a.is_finite() and b.is_finite()):
                return str(a) == str(b)
            return abs(a - b) <= self.options.number_tolerance
        except InvalidOperation:
            return str(left) == str(right)

    def _normal(self, value: object) -> str:
        text = str(value) if not isinstance(value, str) else value
        if not isinstance(value, str):
            return text
        if self.options.ignore_whitespace:
            text = " ".join(text.split())
        if self.options.ignore_case:
            text = text.casefold()
        return text

    def lines(self, left: list[str], right: list[str]) -> None:
        a = [self._normal(line) for line in left]
        b = [self._normal(line) for line in right]
        # The common start and end need no matching, which is most of a typical pair of files.
        start = 0
        while start < len(a) and start < len(b) and a[start] == b[start]:
            start += 1
        end = 0
        while end < len(a) - start and end < len(b) - start and a[-1 - end] == b[-1 - end]:
            end += 1
        self.unchanged += start + end
        middle_a, middle_b = a[start : len(a) - end], b[start : len(b) - end]
        if max(len(middle_a), len(middle_b)) > MAX_LINES_TO_MATCH:
            self.notes.append(
                f"more than {MAX_LINES_TO_MATCH:,} lines differ, so they were compared by position"
            )
            self._lines_by_position(left, right, start, end)
            return
        matcher = difflib.SequenceMatcher(None, middle_a, middle_b, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                self.unchanged += i2 - i1
            else:
                self._line_block(left, right, start + i1, start + i2, start + j1, start + j2)

    def _lines_by_position(self, left: list[str], right: list[str], start: int, end: int) -> None:
        self._line_block(left, right, start, len(left) - end, start, len(right) - end)

    def _line_block(
        self, left: list[str], right: list[str], i1: int, i2: int, j1: int, j2: int
    ) -> None:
        for offset in range(max(i2 - i1, j2 - j1)):
            i, j = i1 + offset, j1 + offset
            if i < i2 and j < j2:
                if self._normal(left[i]) == self._normal(right[j]):
                    self.unchanged += 1
                else:
                    self._scalars(f"line {i + 1}", None, left[i], right[j])
            elif i < i2:
                self._one_side(f"line {i + 1}", None, left[i], Change.REMOVED)
            else:
                self._one_side(f"line {j + 1}", None, right[j], Change.ADDED)
