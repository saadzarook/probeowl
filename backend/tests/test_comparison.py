# Author: @SaadZarook
"""The comparison engine: paths, ignore lists, arrays, tolerance and masking."""

import ast
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from probeowl.comparison import (
    MASKED,
    ArrayMatch,
    Change,
    Comparison,
    Difference,
    Leaf,
    Options,
    Side,
    compare,
    compare_lines,
)
from probeowl.errors import RequestError


def diffs(left: object, right: object, **options: Any) -> list[tuple[str, Change, object, object]]:
    found = compare(left, right, Options(**options)).differences
    return [(d.path, d.change, d.left, d.right) for d in found]


def secret_keys(leaf: Leaf) -> bool:
    return leaf.key in {"password", "token"}


# --- Paths ---------------------------------------------------------------------------------------


def test_identical_trees_have_no_differences() -> None:
    tree = {"a": {"b": [1, 2, {"c": "x"}]}, "d": None}
    result = compare(tree, dict(tree))
    assert result.differences == []
    assert result.unchanged == 4


def test_changes_are_reported_by_dotted_path() -> None:
    left = {"payments": {"retry": {"max": 3}, "timeout_ms": 2000}}
    right = {"payments": {"retry": {"max": 5}, "timeout_ms": 2000}}
    assert diffs(left, right) == [("payments.retry.max", Change.CHANGED, 3, 5)]


def test_added_and_removed_keys() -> None:
    assert diffs({"a": 1, "b": 2}, {"a": 1, "c": 3}) == [
        ("b", Change.REMOVED, 2, None),
        ("c", Change.ADDED, None, 3),
    ]


def test_an_added_subtree_is_reported_leaf_by_leaf() -> None:
    assert diffs({}, {"a": {"b": 1, "c": [2, 3]}}) == [
        ("a.b", Change.ADDED, None, 1),
        ("a.c[0]", Change.ADDED, None, 2),
        ("a.c[1]", Change.ADDED, None, 3),
    ]


def test_an_empty_container_on_one_side_is_still_a_difference() -> None:
    assert [d[:2] for d in diffs({}, {"a": {}})] == [("a", Change.ADDED)]


@pytest.mark.parametrize(
    ("key", "path"),
    [("a.b", 'x["a.b"]'), ("a[0]", 'x["a[0]"]'), ('q"t', 'x["q\\"t"]'), ("", 'x[""]')],
)
def test_a_key_that_would_mislead_is_quoted(key: str, path: str) -> None:
    assert diffs({"x": {key: 1}}, {"x": {key: 2}})[0][0] == path


def test_null_to_a_value_is_a_change_not_an_addition() -> None:
    assert diffs({"a": None}, {"a": 1}) == [("a", Change.CHANGED, None, 1)]


@pytest.mark.parametrize(
    ("left", "right"),
    [({"a": 1}, {"a": {"b": 1}}), ({"a": [1]}, {"a": {"b": 1}})],
    ids=["scalar to mapping", "list to mapping"],
)
def test_a_change_of_shape_is_removed_then_added(left: Any, right: Any) -> None:
    changes = {d[1] for d in diffs(left, right)}
    assert changes == {Change.REMOVED, Change.ADDED}


def test_the_counts_add_up() -> None:
    result = compare({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 9, "d": 4})
    assert (
        result.count(Change.CHANGED),
        result.count(Change.ADDED),
        result.count(Change.REMOVED),
    ) == (
        1,
        1,
        1,
    )
    assert result.unchanged == 1


# --- Scalars -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [(1, "1"), (True, 1), (0, False), (None, ""), ("1.0", 1.0), (None, 0)],
)
def test_values_of_different_types_differ(left: object, right: object) -> None:
    assert len(diffs({"a": left}, {"a": right})) == 1


def test_an_int_and_the_same_float_are_equal() -> None:
    assert diffs({"a": 1}, {"a": 1.0}) == []


@pytest.mark.parametrize(
    ("tolerance", "left", "right", "same"),
    [
        ("0", 1, 2, False),
        ("1", 1, 2, True),
        ("0.5", 1, 2, False),
        ("0.1", 0.1, 0.2, True),
        ("0.01", "1", "1.001", False),
    ],
)
def test_number_tolerance(tolerance: str, left: Any, right: Any, same: bool) -> None:
    found = diffs({"a": left}, {"a": right}, number_tolerance=Decimal(tolerance))
    assert (found == []) is same


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        (float("nan"), float("nan"), True),
        (float("inf"), float("inf"), True),
        (float("inf"), float("-inf"), False),
        (float("nan"), 1, False),
    ],
)
def test_numbers_that_are_not_finite_do_not_crash(left: float, right: float, same: bool) -> None:
    found = diffs({"a": left}, {"a": right}, number_tolerance=Decimal(5))
    assert (found == []) is same


def test_ignore_whitespace_and_case() -> None:
    assert len(diffs({"a": "x  y"}, {"a": "x y"})) == 1
    assert diffs({"a": " x  y\n"}, {"a": "x y"}, ignore_whitespace=True) == []
    assert len(diffs({"a": "ABC"}, {"a": "abc"})) == 1
    assert diffs({"a": "ABC"}, {"a": "abc"}, ignore_case=True) == []


def test_ignoring_case_applies_to_values_not_keys() -> None:
    assert len(diffs({"A": 1}, {"a": 1}, ignore_case=True)) == 2


# --- Ignore paths --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path_left", "ignored"),
    [
        ("build.number", {"build": {"number": 1}}, True),
        ("*.updated_at", {"a": {"b": {"updated_at": 1}}}, True),
        ("*.updated_at", {"updated_at": 1}, True),
        ("*.updated_at", {"not_updated_at": 1}, False),
        ("build.*", {"build": {"number": 1}}, True),
        ("other", {"build": {"number": 1}}, False),
    ],
)
def test_ignore_patterns(pattern: str, path_left: dict[str, Any], ignored: bool) -> None:
    def changed(value: Any) -> Any:
        return {k: changed(v) for k, v in value.items()} if isinstance(value, dict) else 2

    result = compare(path_left, changed(path_left), Options(ignore_paths=(pattern,)))
    assert (result.differences == []) is ignored
    assert result.ignored == (1 if ignored else 0)


def test_an_ignored_key_on_one_side_is_not_added_or_removed() -> None:
    result = compare({"a": 1, "t": 1}, {"a": 1, "u": 2}, Options(ignore_paths=("t", "u")))
    assert result.differences == []
    assert result.ignored == 2


def test_an_ignored_path_inside_an_added_subtree_stays_ignored() -> None:
    result = compare(
        {}, {"a": {"keep": 1, "updated_at": 2}}, Options(ignore_paths=("*.updated_at",))
    )
    assert [d.path for d in result.differences] == ["a.keep"]
    assert result.ignored == 1


def test_an_ignored_path_inside_an_array() -> None:
    left = {"items": [{"id": 1, "updated_at": 1}]}
    right = {"items": [{"id": 1, "updated_at": 2}]}
    assert diffs(left, right, ignore_paths=("*.updated_at",)) == []


def test_square_brackets_in_a_pattern_are_positions_not_character_sets() -> None:
    left = {"items": [1, 2]}
    right = {"items": [1, 3]}
    assert diffs(left, right, ignore_paths=("items[1]",)) == []
    assert len(diffs(left, right, ignore_paths=("items[0]",))) == 1


# --- Key order -----------------------------------------------------------------------------------


def test_key_order_is_ignored_by_default() -> None:
    assert diffs({"a": 1, "b": 2}, {"b": 2, "a": 1}) == []


def test_key_order_can_count() -> None:
    [found] = compare(
        {"a": 1, "b": 2}, {"b": 2, "a": 1}, Options(ignore_key_order=False)
    ).differences
    assert found.change is Change.CHANGED
    assert found.left == "key order: a, b"
    assert found.right == "key order: b, a"


def test_key_order_only_looks_at_the_shared_keys() -> None:
    assert diffs({"a": 1, "x": 0, "b": 2}, {"a": 1, "b": 2}, ignore_key_order=False) == [
        ("x", Change.REMOVED, 0, None)
    ]


# --- Arrays --------------------------------------------------------------------------------------


def test_arrays_by_position() -> None:
    assert diffs({"a": [1, 2, 3]}, {"a": [1, 9]}) == [
        ("a[1]", Change.CHANGED, 2, 9),
        ("a[2]", Change.REMOVED, 3, None),
    ]


def test_ignoring_array_order_matches_anything_equal() -> None:
    left = {"a": [1, 2, {"x": 1}]}
    right = {"a": [{"x": 1}, 2, 1]}
    assert len(diffs(left, right)) > 0
    result = compare(left, right, Options(arrays=ArrayMatch.ANY_ORDER))
    assert result.differences == []
    assert result.unchanged == 3


def test_ignoring_array_order_still_counts_repeats() -> None:
    found = diffs([1, 1, 2], [1, 2, 2], arrays=ArrayMatch.ANY_ORDER)
    assert found == [("[1]", Change.CHANGED, 1, 2)]


def test_ignoring_array_order_names_each_elements_real_position() -> None:
    found = diffs([9, 1, 2], [1, 2], arrays=ArrayMatch.ANY_ORDER)
    assert found == [("[0]", Change.REMOVED, 9, None)]
    found = diffs([1, 2], [1, 2, 9], arrays=ArrayMatch.ANY_ORDER)
    assert found == [("[2]", Change.ADDED, None, 9)]


def test_ignoring_array_order_pairs_what_is_left_so_tolerance_applies() -> None:
    found = diffs([1.0, 5], [5, 1.05], arrays=ArrayMatch.ANY_ORDER, number_tolerance=Decimal("0.1"))
    assert found == []


def test_ignoring_array_order_respects_ignored_paths_inside_elements() -> None:
    left = [{"id": 1, "t": 1}, {"id": 2, "t": 1}]
    right = [{"id": 2, "t": 9}, {"id": 1, "t": 9}]
    assert diffs(left, right, arrays=ArrayMatch.ANY_ORDER, ignore_paths=("*.t",)) == []


def test_ignoring_array_order_stays_quick_on_a_large_array() -> None:
    left = list(range(200_000))
    assert compare(left, left[::-1], Options(arrays=ArrayMatch.ANY_ORDER)).differences == []


ROWS_LEFT = [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 3}]
ROWS_RIGHT = [{"id": "c", "v": 3}, {"id": "a", "v": 9}, {"id": "d", "v": 4}]


def test_arrays_by_key() -> None:
    found = diffs(ROWS_LEFT, ROWS_RIGHT, arrays=ArrayMatch.BY_KEY, array_key="id")
    assert found == [
        ("[id=a].v", Change.CHANGED, 1, 9),
        ("[id=b].id", Change.REMOVED, "b", None),
        ("[id=b].v", Change.REMOVED, 2, None),
        ("[id=d].id", Change.ADDED, None, "d"),
        ("[id=d].v", Change.ADDED, None, 4),
    ]


@pytest.mark.parametrize(
    "rows",
    [
        [{"id": "a"}, {"id": "a"}],
        [{"id": "a"}, {"other": 1}],
        [{"id": "a"}, 5],
        [{"id": {"nested": 1}}],
    ],
    ids=["duplicate", "missing", "not a mapping", "not a scalar"],
)
def test_a_key_that_does_not_work_falls_back_to_position_and_says_so(rows: list[Any]) -> None:
    result = compare(rows, rows, Options(arrays=ArrayMatch.BY_KEY, array_key="id"))
    assert result.differences == []
    assert result.notes and "matched by position" in result.notes[0]


def test_a_masked_key_cannot_go_in_the_path() -> None:
    rows = [{"token": "s3cret", "v": 1}]
    changed = [{"token": "s3cret", "v": 2}]
    result = compare(
        rows, changed, Options(arrays=ArrayMatch.BY_KEY, array_key="token", masker=secret_keys)
    )
    assert "s3cret" not in str(result)
    assert result.differences[0].path == "[0].v"
    assert "matched by position" in result.notes[0]


# --- Masking -------------------------------------------------------------------------------------


def test_a_masked_value_that_differs_says_so_without_either_value() -> None:
    result = compare({"password": "old"}, {"password": "new"}, Options(masker=secret_keys))
    assert result.differences == [Difference("password", Change.CHANGED, MASKED, MASKED, True)]
    assert result.masked == 1
    assert "old" not in str(result) and "new" not in str(result)


def test_a_masked_value_that_is_equal_counts_as_unchanged() -> None:
    result = compare({"password": "same"}, {"password": "same"}, Options(masker=secret_keys))
    assert result.differences == []
    assert result.unchanged == 1


@pytest.mark.parametrize(
    ("left", "right", "change"),
    [
        ({"password": "x"}, {}, Change.REMOVED),
        ({}, {"password": "x"}, Change.ADDED),
        ({"a": {"password": "x"}}, {"a": 1}, Change.REMOVED),
        ({"a": [{"password": "x"}]}, {}, Change.REMOVED),
    ],
)
def test_a_masked_value_on_one_side_is_never_shown(left: Any, right: Any, change: Change) -> None:
    result = compare(left, right, Options(masker=secret_keys))
    found = [d for d in result.differences if d.change is change]
    assert found and found[0].masked
    assert "'x'" not in str(result)


def test_a_masker_can_look_at_the_value_and_either_side_masks_both() -> None:
    def connection_strings(leaf: Leaf) -> bool:
        return isinstance(leaf.value, str) and "://" in leaf.value

    result = compare({"a": "plain"}, {"a": "db://u:p@h"}, Options(masker=connection_strings))
    assert result.differences == [Difference("a", Change.CHANGED, MASKED, MASKED, True)]
    assert "plain" not in str(result)


def test_the_masker_is_told_each_leaf_on_each_side() -> None:
    seen: list[Leaf] = []

    def record(leaf: Leaf) -> bool:
        seen.append(leaf)
        return False

    compare({"a": {"b": "x"}}, {"a": {"b": "y"}}, Options(masker=record))
    assert seen == [Leaf(Side.LEFT, "a.b", "b", "x"), Leaf(Side.RIGHT, "a.b", "b", "y")]


# --- Lines ---------------------------------------------------------------------------------------


def lines(left: str, right: str, **options: Any) -> list[tuple[str, Change, object, object]]:
    found = compare_lines(left, right, Options(**options)).differences
    return [(d.path, d.change, d.left, d.right) for d in found]


def test_text_is_compared_line_by_line() -> None:
    assert lines("a\nb\nc\n", "a\nX\nc\n") == [("line 2", Change.CHANGED, "b", "X")]


def test_a_line_added_near_the_start_does_not_shift_the_rest() -> None:
    assert lines("a\nb\nc\n", "a\nnew\nb\nc\n") == [("line 2", Change.ADDED, None, "new")]


def test_a_line_removed() -> None:
    assert lines("a\nb\nc\n", "a\nc\n") == [("line 2", Change.REMOVED, "b", None)]


def test_lines_honour_whitespace_and_case() -> None:
    assert lines(" A  b\n", "a b\n", ignore_whitespace=True, ignore_case=True) == []


def test_line_counts() -> None:
    assert compare_lines("a\nb\nc\n", "a\nX\nc\n").unchanged == 2


def test_a_masked_line_stays_masked() -> None:
    def everything(leaf: Leaf) -> bool:
        return True

    result = compare_lines("secret\n", "other\n", Options(masker=everything))
    assert result.differences[0].masked
    assert "secret" not in str(result)


def test_huge_texts_that_differ_throughout_are_compared_by_position_and_say_so() -> None:
    left = "\n".join(f"a{i}" for i in range(30_000))
    right = "\n".join(f"b{i}" for i in range(30_000))
    result = compare_lines(left, right)
    assert len(result.differences) == 30_000
    assert "compared by position" in result.notes[0]


def test_empty_texts() -> None:
    assert compare_lines("", "") == Comparison([], 0, 0)
    assert lines("", "a\n") == [("line 1", Change.ADDED, None, "a")]


# --- Failure and purity --------------------------------------------------------------------------


def test_nesting_too_deep_is_a_clear_error() -> None:
    deep: Any = 1
    for _ in range(sys.getrecursionlimit() * 2):
        deep = [deep]
    with pytest.raises(RequestError, match="nested too deeply"):
        compare(deep, deep)


def test_the_engine_reads_nothing_outside_itself() -> None:
    allowed = {"difflib", "fnmatch", "json", "collections.abc", "dataclasses", "decimal", "enum"}
    source = (Path(__file__).parents[1] / "probeowl" / "comparison.py").read_text(encoding="utf-8")
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    assert found - allowed == {"probeowl.errors"}
