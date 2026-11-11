# Author: @SaadZarook
"""The expression language of expectations and conditions.

    record.status in ['succeeded', 'failed', 'pending']
    payment.amount == record.amount
    count(ledger) == 2
    sum(ledger.amount where direction == 'credit') == sum(ledger.amount where direction == 'debit')
    not exists(ledger)
    unique(cards.card_id)
    all(cards, controller.status == status)     # controller: a lookup per card (fan-out)

Expressions are parsed once, when a probe is loaded, and evaluated against the rows each
location returned. Nothing in an expression can call out of this module.
"""

import operator
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from probeowl.errors import ConfigError

# --- Syntax tree ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    """A number, string, true, false or null."""

    value: Any


@dataclass(frozen=True)
class ListOf:
    """A list literal, for `in`."""

    items: tuple["Node", ...]


@dataclass(frozen=True)
class Ref:
    """A field: `record.status`, or a bare `status` inside a where or all/any/none."""

    parts: tuple[str, ...]


@dataclass(frozen=True)
class Unary:
    """`not x` or `-x`."""

    op: str
    operand: "Node"


@dataclass(frozen=True)
class Binary:
    """A comparison, `in`, `and`, `or` or arithmetic."""

    op: str
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Rows:
    """The rows of a location, optionally one field of them, optionally filtered."""

    location: str
    field: str | None
    where: "Node | None"


@dataclass(frozen=True)
class Call:
    """count, exists, sum, min, max, unique, all, any, none or now."""

    name: str
    rows: Rows | None
    predicate: "Node | None"


Node = Literal | ListOf | Ref | Unary | Binary | Call

_COMPARISONS = ("==", "!=", "<=", ">=", "<", ">")
_OPERATORS: dict[str, Callable[[Any, Any], Any]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
_SET_FUNCTIONS = {"count", "exists", "sum", "min", "max", "unique", "all", "any", "none"}
_PREDICATE_FUNCTIONS = {"all", "any", "none"}
_FIELD_FUNCTIONS = {"sum", "min", "max", "unique"}
# Only these mean something on no rows; anything else would pass on data that is not there.
COUNTING_FUNCTIONS = {"count", "exists", "none"}
_TOKEN = re.compile(
    r"\s*(?:(?P<number>\d+(?:\.\d+)?)|(?P<string>'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>==|!=|<=|>=|[<>+\-*/()\[\],.]))"
)


# --- Parsing --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str


def parse(text: str) -> Node:
    """Parse one expectation or condition; ConfigError if it is not valid."""
    try:
        parser = _Parser(_tokenize(text))
        node = parser.expression()
        parser.end()
    except ValueError as err:
        raise ConfigError(f"cannot read {text!r}: {err}") from err
    return node


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    while position < len(text.rstrip()):
        match = _TOKEN.match(text, position)
        if match is None or match.end() == position:
            raise ValueError(f"unexpected {text[position:].strip()[:10]!r}")
        kind = match.lastgroup
        assert kind is not None  # the pattern only matches through a named group
        tokens.append(_Token(kind, match.group(kind)))
        position = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self._tokens = tokens
        self._at = 0

    def end(self) -> None:
        if self._at != len(self._tokens):
            raise ValueError(f"unexpected {self._tokens[self._at].text!r}")

    def expression(self) -> Node:
        node = self._and()
        while self._accept("or"):
            node = Binary("or", node, self._and())
        return node

    def _and(self) -> Node:
        node = self._not()
        while self._accept("and"):
            node = Binary("and", node, self._not())
        return node

    def _not(self) -> Node:
        if self._accept("not"):
            return Unary("not", self._not())
        return self._comparison()

    def _comparison(self) -> Node:
        left = self._sum()
        for op in _COMPARISONS:
            if self._accept(op):
                return Binary(op, left, self._sum())
        if self._accept("in"):
            return Binary("in", left, self._sum())
        if self._peek("not") and self._peek("in", 1):
            self._at += 2
            return Unary("not", Binary("in", left, self._sum()))
        return left

    def _sum(self) -> Node:
        node = self._term()
        while (op := self._accept("+") or self._accept("-")) is not None:
            node = Binary(op, node, self._term())
        return node

    def _term(self) -> Node:
        node = self._unary()
        while (op := self._accept("*") or self._accept("/")) is not None:
            node = Binary(op, node, self._unary())
        return node

    def _unary(self) -> Node:
        if self._accept("-"):
            return Unary("-", self._unary())
        return self._primary()

    def _primary(self) -> Node:
        token = self._next()
        if token.kind == "number":
            return Literal(Decimal(token.text))
        if token.kind == "string":
            quote = token.text[0]
            return Literal(token.text[1:-1].replace(quote * 2, quote))
        if token.text == "(":
            node = self.expression()
            self._expect(")")
            return node
        if token.text == "[":
            items: list[Node] = []
            while not self._accept("]"):
                if items:
                    self._expect(",")
                items.append(self.expression())
            return ListOf(tuple(items))
        if token.kind == "name":
            keyword = {"true": True, "false": False, "null": None}
            if token.text in keyword:
                return Literal(keyword[token.text])
            if self._accept("("):
                return self._call(token.text)
            return Ref(self._dotted(token.text))
        raise ValueError(f"unexpected {token.text!r}")

    def _call(self, name: str) -> Node:
        if name == "now":
            self._expect(")")
            return Call("now", None, None)
        if name not in _SET_FUNCTIONS:
            raise ValueError(f"unknown function {name}()")
        parts = self._dotted(self._next_name())
        if len(parts) > 2:
            raise ValueError(f"{'.'.join(parts)} is not a location or location.field")
        where = self.expression() if self._accept("where") else None
        rows = Rows(parts[0], parts[1] if len(parts) == 2 else None, where)
        if (name in _FIELD_FUNCTIONS) != (rows.field is not None):
            raise ValueError(
                f"{name}() takes {'location.field' if name in _FIELD_FUNCTIONS else 'a location'}"
            )
        predicate = None
        if name in _PREDICATE_FUNCTIONS:
            self._expect(",")
            predicate = self.expression()
        self._expect(")")
        return Call(name, rows, predicate)

    def _dotted(self, first: str) -> tuple[str, ...]:
        parts = [first]
        while self._accept("."):
            parts.append(self._next_name())
        return tuple(parts)

    def _next_name(self) -> str:
        token = self._next()
        if token.kind != "name":
            raise ValueError(f"expected a name, found {token.text!r}")
        return token.text

    def _next(self) -> _Token:
        if self._at >= len(self._tokens):
            raise ValueError("it ends too early")
        token = self._tokens[self._at]
        self._at += 1
        return token

    def _peek(self, text: str, ahead: int = 0) -> bool:
        index = self._at + ahead
        return index < len(self._tokens) and self._tokens[index].text == text

    def _accept(self, text: str) -> str | None:
        if self._peek(text):
            self._at += 1
            return text
        return None

    def _expect(self, text: str) -> None:
        if self._accept(text) is None:
            found = self._tokens[self._at].text if self._at < len(self._tokens) else "the end"
            raise ValueError(f"expected {text!r}, found {found!r}")


def locations_used(node: Node) -> set[str]:
    """The locations an expression reads, for checks at load time."""
    found: set[str] = set()

    def visit(n: Node | None, in_rows: bool) -> None:
        if isinstance(n, Ref) and (not in_rows or len(n.parts) > 1):
            found.add(n.parts[0])
        elif isinstance(n, Unary):
            visit(n.operand, in_rows)
        elif isinstance(n, Binary):
            visit(n.left, in_rows)
            visit(n.right, in_rows)
        elif isinstance(n, ListOf):
            for item in n.items:
                visit(item, in_rows)
        elif isinstance(n, Call) and n.rows is not None:
            found.add(n.rows.location)
            visit(n.rows.where, True)
            visit(n.predicate, True)

    visit(node, False)
    return found


# --- Evaluation -----------------------------------------------------------------------------


class Unavailable(Exception):
    """An expression needs a location whose data could not be read."""

    def __init__(self, location: str, outcome: str, message: str | None = None):
        super().__init__(message or f"needs {location}, which is {outcome}")
        self.location = location
        self.outcome = outcome


class Data(Protocol):
    """The rows each location returned, as the evaluator sees them."""

    def rows(self, location: str, *, counting: bool = False) -> list[dict[str, Any]]:
        """All rows of a location; raises Unavailable if it could not be read.

        A Missing location is no rows only when counting, for count(), exists() and none();
        any other read of it raises Unavailable.
        """
        ...

    def fanned_out(self, location: str, row: dict[str, Any]) -> list[dict[str, Any]] | None:
        """The rows a fan-out location found for one row of its parent.

        None when location does not fan out from that row's location. Raises Unavailable
        when the lookup for that row could not be read, was not made, or found nothing.
        """
        ...


def evaluate(node: Node, data: Data, row: dict[str, Any] | None = None) -> Any:
    """The value of an expression. A bare name reads from row, inside where and all/any."""
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, ListOf):
        return [evaluate(item, data, row) for item in node.items]
    if isinstance(node, Ref):
        return _ref(node, data, row)
    if isinstance(node, Unary):
        value = evaluate(node.operand, data, row)
        return (not _truthy(value)) if node.op == "not" else _arithmetic("-", Decimal(0), value)
    if isinstance(node, Binary):
        return _binary(node, data, row)
    return _call(node, data)


def _ref(node: Ref, data: Data, row: dict[str, Any] | None) -> Any:
    if row is not None and len(node.parts) == 1:
        return row.get(node.parts[0])
    if len(node.parts) == 1:
        raise ConfigError(f"{node.parts[0]} needs a location: write location.{node.parts[0]}")
    # Inside all(cards, ...), controller.status reads the controller lookup for that card.
    fanned = data.fanned_out(node.parts[0], row) if row is not None else None
    rows = fanned if fanned is not None else data.rows(node.parts[0])
    # A location's field outside an aggregate reads its one record. A Missing location
    # never gets here: its field is not null but unknown, so the read raised Unavailable.
    value: Any = rows[0]
    for part in node.parts[1:]:
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _binary(node: Binary, data: Data, row: dict[str, Any] | None) -> Any:
    if node.op == "and":
        return _truthy(evaluate(node.left, data, row)) and _truthy(evaluate(node.right, data, row))
    if node.op == "or":
        return _truthy(evaluate(node.left, data, row)) or _truthy(evaluate(node.right, data, row))
    left = evaluate(node.left, data, row)
    right = evaluate(node.right, data, row)
    if node.op == "in":
        return isinstance(right, list) and any(compare("==", left, item) for item in right)
    if node.op in _COMPARISONS:
        return compare(node.op, left, right)
    return _arithmetic(node.op, left, right)


def _call(node: Call, data: Data) -> Any:
    if node.name == "now":
        return datetime.now(UTC)
    assert node.rows is not None  # every function but now() takes rows
    rows = matching_rows(node, data)
    if node.name == "count":
        return Decimal(len(rows))
    if node.name == "exists":
        return bool(rows)
    if node.name in _PREDICATE_FUNCTIONS:
        assert node.predicate is not None  # the parser requires one
        results = [_truthy(evaluate(node.predicate, data, r)) for r in rows]
        if node.name == "all":
            return all(results)
        return any(results) if node.name == "any" else not any(results)
    assert node.rows.field is not None  # sum, min, max and unique take location.field
    if node.name == "unique":
        return not duplicates(rows, node.rows.field)
    values = [n for r in rows if (n := _number(r.get(node.rows.field))) is not None]
    if node.name == "sum":
        return sum(values, Decimal(0))
    pick: Callable[[Sequence[Decimal]], Decimal] = min if node.name == "min" else max
    return pick(values) if values else None


def matching_rows(node: Call, data: Data) -> list[dict[str, Any]]:
    """The rows a set function looks at: its location's, filtered by its where."""
    assert node.rows is not None  # only set functions have rows
    return [
        r
        for r in data.rows(node.rows.location, counting=node.name in COUNTING_FUNCTIONS)
        if node.rows.where is None or _truthy(evaluate(node.rows.where, data, r))
    ]


def failing_rows(node: Call, data: Data) -> list[dict[str, Any]]:
    """The rows that make all() or none() false: those all() rejects, those none() finds."""
    assert node.predicate is not None  # all and none take a predicate
    wanted = node.name == "all"
    return [
        r for r in matching_rows(node, data) if _truthy(evaluate(node.predicate, data, r)) != wanted
    ]


def duplicates(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    """Values of field found in more than one row, with how often. Nulls are not values."""
    seen: dict[str, int] = {}
    for row in rows:
        value = row.get(field)
        if value is not None:
            # Compared by meaning, like ==: 500 and '500.00' are the same value.
            number = _number(value)
            key = format(number.normalize(), "f") if number is not None else str(value)
            seen[key] = seen.get(key, 0) + 1
    return {value: n for value, n in seen.items() if n > 1}


def compare(op: str, left: Any, right: Any) -> bool:
    """Compare by meaning: 500, 500.00 and '500.00' are equal; null equals only null."""
    if left is None or right is None:
        return {"==": left is right, "!=": left is not right}.get(op, False)
    if isinstance(left, bool) != isinstance(right, bool):
        # Python counts True as 1; in a probe a boolean only equals a boolean.
        return op == "!="
    a, b = _comparable(left, right)
    try:
        return bool(_OPERATORS[op](a, b))
    except TypeError:
        # Values of kinds that have no order, such as a number and a date.
        return op == "!="


def _comparable(left: Any, right: Any) -> tuple[Any, Any]:
    a, b = _number(left), _number(right)
    if (
        a is not None
        and b is not None
        and not isinstance(left, bool)
        and not isinstance(right, bool)
    ):
        return a, b
    if isinstance(left, datetime | date) or isinstance(right, datetime | date):
        return _moment(left), _moment(right)
    return left, right


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal | int | float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


def _moment(value: Any) -> Any:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    return value


def _arithmetic(op: str, left: Any, right: Any) -> Decimal | None:
    a, b = _number(left), _number(right)
    if a is None or b is None:
        return None
    if op == "/":
        return a / b if b != 0 else None
    return {"+": a + b, "-": a - b, "*": a * b}[op]


def _truthy(value: Any) -> bool:
    return value is True or (value not in (None, False, "", 0) and not isinstance(value, bool))
