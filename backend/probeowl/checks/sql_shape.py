# Author: @SaadZarook
"""What a lookup's SQL reads and how it filters, for the label rules of the save checks."""

from dataclasses import dataclass
from enum import Enum

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class Shape:
    """The tables a SELECT reads (alias -> table, lower case) and its top-level conditions."""

    tables: dict[str, str]
    conditions: list[exp.Expr]
    projections: list[exp.Expr]


def shape(sql: str, dialect: str) -> Shape:
    """Parse one SELECT; ParseError propagates (the classifier reports it first)."""
    tree = sqlglot.parse_one(sql, read=dialect)
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = {
        (t.alias_or_name or t.name).lower(): t.name.lower()
        for t in tree.find_all(exp.Table)
        if t.name.lower() not in ctes
    }
    where = tree.args.get("where") if isinstance(tree, exp.Select) else None
    projections = list(tree.expressions) if isinstance(tree, exp.Select) else []
    return Shape(tables, _conjuncts(where.this) if where is not None else [], projections)


def parameters_of(sql: str, dialect: str) -> set[str]:
    """The :name parameters the SELECT binds."""
    return {
        placeholder.name
        for placeholder in sqlglot.parse_one(sql, read=dialect).find_all(exp.Placeholder)
        if placeholder.name
    }


def table_of(column: exp.Column, tables: dict[str, str]) -> str | None:
    """The table a column belongs to: by its qualifier, or the only table read."""
    if column.table:
        return tables.get(column.table.lower())
    names = set(tables.values())
    return next(iter(names)) if len(names) == 1 else None


def keyed(conditions: list[exp.Expr], table: str, tables: dict[str, str], keys: set[str]) -> bool:
    """Is some top-level condition `<key column of table> = :parameter` (or IN parameters)?

    keys: the table's key columns in lower case (primary key, link, external key).
    """
    for condition in conditions:
        column = _key_column(condition)
        if column is not None and table_of(column, tables) == table and column.name.lower() in keys:
            return True
    return False


# A key lookup reads a few rows by equality: = or a short fixed IN list, never a range.
MAX_IN_VALUES = 10


def _key_column(condition: exp.Expr) -> exp.Column | None:
    if isinstance(condition, exp.EQ):
        for column, other in (
            (condition.this, condition.expression),
            (condition.expression, condition.this),
        ):
            if isinstance(column, exp.Column) and _fixed(other):
                return column
    if isinstance(condition, exp.In) and isinstance(condition.this, exp.Column):
        values = condition.expressions
        if 0 < len(values) <= MAX_IN_VALUES and all(_fixed(v) for v in values):
            return condition.this
    return None


def _fixed(value: exp.Expr) -> bool:
    return isinstance(value, exp.Placeholder | exp.Literal)


class TimeBound(Enum):
    """How a lookup bounds a table's time column."""

    BOUNDED = "bounded"  # from a parameter, or relative to now
    CONSTANT = "constant"  # from a fixed value, which may reach far into the past
    NONE = "none"  # no lower bound


# How the dialects say "now", as sqlglot reads them; anything relative to now builds on one.
_NOW = (exp.CurrentTimestamp, exp.CurrentDate, exp.CurrentDatetime, exp.CurrentTime)
_NOW_FUNCTIONS = {
    "NOW",
    "SYSTIMESTAMP",
    "SYSDATE",
    "SYSDATETIME",
    "SYSUTCDATETIME",
    "GETUTCDATE",
    "UTC_TIMESTAMP",
    "LOCALTIMESTAMP",
}


def time_bound(
    conditions: list[exp.Expr], table: str, time_column: str, tables: dict[str, str]
) -> TimeBound:
    """The best lower bound on the table's time column among the top-level conditions."""
    found = TimeBound.NONE
    for condition in conditions:
        bound = _lower_bound(condition, table, time_column, tables)
        if bound is None:
            continue
        if _relative(bound):
            return TimeBound.BOUNDED
        found = TimeBound.CONSTANT
    return found


def _lower_bound(
    condition: exp.Expr, table: str, time_column: str, tables: dict[str, str]
) -> exp.Expr | None:
    """What the condition says the time column is at least, if it says so."""

    def is_time(node: exp.Expr) -> bool:
        return (
            isinstance(node, exp.Column)
            and node.name.lower() == time_column.lower()
            and table_of(node, tables) == table
        )

    if isinstance(condition, exp.Between) and is_time(condition.this):
        low: exp.Expr = condition.args["low"]
        return low
    if not isinstance(condition, exp.GT | exp.GTE | exp.LT | exp.LTE | exp.EQ):
        return None
    left, right = condition.this, condition.expression
    # created_at >= :since, :since <= created_at, created_at = :at; never created_at < :until.
    if is_time(left) and isinstance(condition, exp.GT | exp.GTE | exp.EQ):
        return right
    if is_time(right) and isinstance(condition, exp.LT | exp.LTE | exp.EQ):
        return left
    return None


def _relative(bound: exp.Expr) -> bool:
    """Is the bound a parameter, or an expression relative to now?"""
    for node in bound.walk():
        if isinstance(node, (exp.Placeholder, *_NOW)):
            return True
        if isinstance(node, exp.Func) and node.sql_name().upper() in _NOW_FUNCTIONS:
            return True
        if isinstance(node, exp.Anonymous) and str(node.this).upper() in _NOW_FUNCTIONS:
            return True
    return False


def selected(
    projections: list[exp.Expr], tables: dict[str, str]
) -> list[tuple[str | None, str, str]]:
    """Each selected column as (table, source column, name in the rows); * as ('*' per table)."""
    found: list[tuple[str | None, str, str]] = []
    for projection in projections:
        if isinstance(projection, exp.Star):
            found.extend((table, "*", "*") for table in set(tables.values()))
            continue
        source = projection.unalias()
        if isinstance(source, exp.Column) and isinstance(source.this, exp.Star):
            found.append((tables.get(source.table.lower()), "*", "*"))
        elif isinstance(source, exp.Column):
            found.append((table_of(source, tables), source.name, projection.alias_or_name))
    return found


def _conjuncts(node: exp.Expr) -> list[exp.Expr]:
    if isinstance(node, exp.And):
        return _conjuncts(node.left) + _conjuncts(node.right)
    if isinstance(node, exp.Paren):
        return _conjuncts(node.this)
    return [node]


class Conversion(Enum):
    """What the SQL does to a parameter's offset before comparing it."""

    DROPS = "drops"  # CAST(:at AS datetime2), SYS_EXTRACT_UTC(:at)
    SETS = "sets"  # TODATETIMEOFFSET(:at, '+04:00'), FROM_TZ(:at, '+04:00')


@dataclass(frozen=True)
class Comparison:
    """A column compared with a :parameter, and what the SQL does to the parameter's offset."""

    parameter: str
    table: str  # lower case
    column: str  # lower case
    conversion: Conversion | None  # the outermost one; None: none, or one the server decides


_COMPARED = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.In)
_OFFSET_TYPES = {exp.DataType.Type.TIMESTAMPTZ, exp.DataType.Type.TIMESTAMPLTZ}
_FUNCTIONS = {
    "SYS_EXTRACT_UTC": Conversion.DROPS,  # Oracle: the UTC TIMESTAMP it stands for
    "TODATETIMEOFFSET": Conversion.SETS,  # SQL Server
    "FROM_TZ": Conversion.SETS,  # Oracle
}
# A conversion whose offset the server decides: CAST(:at AS datetimeoffset) is read as +00:00.
_DECIDED = "decided"


def comparisons(sql: str, dialect: str) -> list[Comparison]:
    """Each place the SELECT compares a plain column of a table it reads with a parameter.

    A column inside a function or conversion is left out: the SQL already decides its type.
    """
    tree = sqlglot.parse_one(sql, read=dialect)
    tables = shape(sql, dialect).tables
    found = []
    for node in tree.find_all(*_COMPARED):
        for column, other in _sides(node):
            table = table_of(column, tables) if isinstance(column, exp.Column) else None
            if table is None or not isinstance(column, exp.Column):
                continue
            for placeholder in other.find_all(exp.Placeholder):
                if placeholder.name:
                    conversion = _conversion(placeholder, other, dialect)
                    found.append(
                        Comparison(placeholder.name, table, column.name.lower(), conversion)
                    )
    return found


def placeholder_conversions(sql: str, dialect: str) -> dict[str, list[Conversion | None]]:
    """For each :parameter, what the SQL does to its offset at each place it is used."""
    tree = sqlglot.parse_one(sql, read=dialect)
    found: dict[str, list[Conversion | None]] = {}
    for placeholder in tree.find_all(exp.Placeholder):
        if placeholder.name:
            conversion = _conversion(placeholder, tree, dialect)
            found.setdefault(placeholder.name, []).append(conversion)
    return found


def _sides(node: exp.Expr) -> list[tuple[exp.Expr, exp.Expr]]:
    if isinstance(node, exp.Between):
        return [(node.this, node.args["low"]), (node.this, node.args["high"])]
    if isinstance(node, exp.In):
        return [(node.this, value) for value in node.expressions]
    return [(node.this, node.expression), (node.expression, node.this)]


def _conversion(placeholder: exp.Expr, side: exp.Expr, dialect: str) -> Conversion | None:
    # The outermost conversion decides; DATEADD and arithmetic keep the type, so they pass.
    found: Conversion | str | None = None
    node: exp.Expr | None = placeholder
    while node is not None:
        found = _converts(node, dialect) or found
        if node is side:
            break
        node = node.parent
    return found if isinstance(found, Conversion) else None


def _converts(node: exp.Expr, dialect: str) -> Conversion | str | None:
    if isinstance(node, exp.Cast):  # CAST, TRY_CAST
        return _DECIDED if node.to.this in _OFFSET_TYPES else Conversion.DROPS
    if isinstance(node, exp.Convert) and isinstance(node.this, exp.DataType):  # TRY_CONVERT too
        return _DECIDED if node.this.this in _OFFSET_TYPES else Conversion.DROPS
    if isinstance(node, exp.AtTimeZone):
        # SQL Server gives a value without an offset the zone's; with one, it keeps one. Oracle
        # first reads a plain TIMESTAMP in the session's time zone, so the server decides.
        return Conversion.SETS if dialect == "tsql" else _DECIDED
    if isinstance(node, exp.Anonymous):
        return _FUNCTIONS.get(str(node.this).upper())
    return None
