# Author: @SaadZarook
"""The statement guard: only a single SELECT runs, with bound parameters and a row cap.

Every lookup's SQL goes through prepare() before a connector sees it. Anything the guard
cannot parse is refused: when in doubt, the probe does not run.

What runs is the SQL as written, never sqlglot's rendering of it: the guard checks the parsed
query, then fills in only the parameters and the row cap, at the positions of their tokens. So
what was reviewed is what runs, comments included, and a comment the database itself would run
is refused, since the parse does not see it.
"""

import itertools
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import ParseError
from sqlglot.tokens import Token, TokenType

from probeowl.errors import GuardViolation
from probeowl.guards.dialects import DialectRules

ParameterValue = str | int | float | Decimal | bool | date | datetime | time | None

# Statements and clauses that change data, take locks, or leave the probe's own query.
# DML and DDL are sqlglot mixins, not Expression subclasses, hence the plain `type`.
_FORBIDDEN: dict[type, str] = {
    exp.DML: "a data change",  # INSERT, UPDATE, DELETE, MERGE, also inside a CTE
    exp.DDL: "a schema change",
    exp.Into: "SELECT INTO",  # creates a table
    exp.Lock: "a locking clause",  # FOR UPDATE, FOR SHARE, LOCK IN SHARE MODE
    exp.WithTableHint: "a table hint",  # NOLOCK, HOLDLOCK, UPDLOCK, TABLOCKX and the rest
    exp.QueryOption: "a query hint",  # OPTION (MAXDOP 64), OPTION (MAXRECURSION 0) and the rest
    exp.Hint: "an optimizer hint",  # /*+ PARALLEL(8) */, /*+ MAX_EXECUTION_TIME(1) */
    exp.Command: "a command",
    exp.Execute: "a procedure call",
}

# Comments a database runs, which sqlglot reads as plain comments: MariaDB's /*! ... */ and
# /*M! ... */, and optimizer hints, /*+ ... */ and Oracle's --+ ... .
_RUN_COMMENTS = ("/*!", "/*M!", "/*+", "--+")


@dataclass(frozen=True)
class GuardedQuery:
    """SQL ready for a driver, its parameters in the driver's style, and the guards applied."""

    sql: str
    parameters: list[ParameterValue] | dict[str, ParameterValue]
    names: list[str]  # the parameter behind each ? in order, or each name once
    guards: list[str]


def prepare(
    sql: str,
    parameters: Mapping[str, object],
    rules: DialectRules,
    max_rows: int,
    allowed_databases: Collection[str] = (),
) -> GuardedQuery:
    """Check sql is a single read-only SELECT, cap its rows, and bind its parameters.

    allowed_databases: the databases a qualified table name may name; any other is refused.
    """
    if max_rows < 1:
        raise GuardViolation(f"the row cap must be at least 1, not {max_rows}")
    if "\x00" in sql:
        raise GuardViolation("the query contains a NUL character")
    tree = _parse_single_select(sql, rules)
    _refuse_forbidden(tree, rules)
    _refuse_other_databases(tree, rules, {d.lower() for d in allowed_databases})
    tokens = Dialect.get_or_raise(rules.sqlglot_dialect).tokenize(sql)
    _refuse_run_comments(sql, tokens)
    names = sorted({p.name for p in tree.find_all(exp.Placeholder)})
    values = {name: _value(name, parameters) for name in names}
    placed = _placeholders(tokens, tree)
    edits = [_parameter(start, end, name, rules) for start, end, name in placed]
    edits += _cap(tree, tokens, rules, max_rows)
    if rules.query_options is not None:
        at = tokens[-1].end + 1
        edits.append(_Edit(at, at, f" {rules.query_options}"))
    sent = _apply(sql, edits, double_percent=rules.param_style == "pyformat")
    order = [name for _, _, name in placed]
    bound: list[ParameterValue] | dict[str, ParameterValue] = values
    if rules.param_style == "qmark":
        bound = [values[name] for name in order]
    guards = [
        "single SELECT",
        f"row cap {max_rows} ({rules.row_cap})",
        f"{len(values)} parameters bound, none interpolated",
        "runs as written: only its parameters and row cap are filled in",
    ]
    names_sent = order if rules.param_style == "qmark" else list(values)
    return GuardedQuery(sql=sent, parameters=bound, names=names_sent, guards=guards)


def count_query(sql: str, rules: DialectRules) -> str:
    """A query counting the rows sql matches, for "showing 50 of 312". Guard it like any other."""
    inner = _parse_single_select(sql, rules)
    if inner.args.get("limit") is None:
        # Order does not change a count, and SQL Server refuses ORDER BY in a derived table.
        inner.set("order", None)
    total = exp.select(exp.Count(this=exp.Star()).as_("total")).from_(inner.subquery("counted"))
    return total.sql(rules.sqlglot_dialect)


def is_single_select(sql: str, rules: DialectRules) -> bool:
    """Whether sql is one SELECT that changes nothing. Anything else is a write query."""
    try:
        tree = _parse_single_select(sql, rules)
    except GuardViolation:
        return False  # what cannot be read as a SELECT cannot be trusted to be one
    return not any(isinstance(node, exp.DML | exp.DDL | exp.Into) for node in tree.walk())


def _parse_single_select(sql: str, rules: DialectRules) -> exp.Query:
    try:
        statements = [s for s in sqlglot.parse(sql, read=rules.sqlglot_dialect) if s is not None]
    except ParseError as err:
        raise GuardViolation(f"the query could not be parsed as {rules.name} SQL: {err}") from err
    if len(statements) != 1:
        raise GuardViolation(f"only a single statement may run; found {len(statements)}")
    [tree] = statements
    if not isinstance(tree, exp.Select | exp.SetOperation):
        raise GuardViolation(f"only a SELECT may run; found {tree.key.upper()}")
    return tree


def _refuse_forbidden(tree: exp.Query, rules: DialectRules) -> None:
    for node in tree.walk():
        for check in (
            _forbidden_node,
            _forbidden_parameter,
            _forbidden_call,
            _forbidden_remote,
            _forbidden_temp_table,
        ):
            reason = check(node, rules)
            if reason is not None:
                raise GuardViolation(reason)


def _forbidden_node(node: exp.Expr, rules: DialectRules) -> str | None:
    for kind, what in _FORBIDDEN.items():
        if isinstance(node, kind):
            return f"{what} is not allowed: {node.sql(rules.sqlglot_dialect)}"
    return None


def _forbidden_parameter(node: exp.Expr, rules: DialectRules) -> str | None:
    if isinstance(node, exp.Parameter):
        # @name and @@name are session variables, not bound parameters.
        return f"use :name parameters, not {node.sql(rules.sqlglot_dialect)}"
    # An anonymous ? reports its name as "?"; only :name placeholders carry `this`.
    if isinstance(node, exp.Placeholder) and node.args.get("this") is None:
        return "use named parameters (:name), not ?"
    return None


def _forbidden_call(node: exp.Expr, rules: DialectRules) -> str | None:
    if isinstance(node, exp.Func):
        name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).upper()
        if name in rules.forbidden_functions:
            return f"{name}() is not allowed in {rules.name} probes"
    qualified = isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func)
    if rules.forbid_qualified_calls and qualified:
        return f"package calls are not allowed: {node.sql(rules.sqlglot_dialect)}"
    return None


def _forbidden_remote(node: exp.Expr, rules: DialectRules) -> str | None:
    # A probe reads the database its binding points at, never another one through it.
    if not isinstance(node, exp.Table):
        return None
    name = node.sql(rules.sqlglot_dialect)
    # sqlglot keeps an Oracle database link in the table's name: t@remote_db.
    if any("@" in part.name for part in node.find_all(exp.Identifier)):
        return f"database links are not allowed: {name}"
    # A four-part name (server.database.schema.table) goes through a SQL Server linked server.
    if isinstance(node.this, exp.Dot):
        return f"linked servers are not allowed: {name}"
    return None


def _forbidden_temp_table(node: exp.Expr, rules: DialectRules) -> str | None:
    # A probe cannot create one, so #t is someone else's, or ##t any session's: not probe data.
    if not isinstance(node, exp.Table):
        return None
    for part in node.find_all(exp.Identifier):
        if part.args.get("temporary") or part.args.get("global_"):
            return f"temp tables are not allowed: {node.sql(rules.sqlglot_dialect)}"
    return None


def _refuse_other_databases(tree: exp.Query, rules: DialectRules, allowed: set[str]) -> None:
    if rules.database_part is None:
        return
    for table in tree.find_all(exp.Table):
        database = table.args.get(rules.database_part)
        if database is not None and database.name.lower() not in allowed:
            names = ", ".join(sorted(allowed)) or "none"
            raise GuardViolation(
                f"{table.sql(rules.sqlglot_dialect)} names database {database.name}; "
                f"this binding allows: {names} (allowed_databases)"
            )


@dataclass(frozen=True)
class _Edit:
    start: int  # the first character replaced
    end: int  # one past the last; equal to start for an insertion
    text: str


def _refuse_run_comments(sql: str, tokens: list[Token]) -> None:
    for token in tokens:
        if token.token_type == TokenType.HINT:
            raise GuardViolation(f"an optimizer hint is not allowed: {token.text}")
    # Between two tokens there is only space and comments: strings and quoted names are tokens.
    # So a comment opener found there opens a comment, whatever the dialect's tokenizer made of it.
    starts = [token.start for token in tokens] + [len(sql)]
    ends = [-1] + [token.end for token in tokens]
    for end, start in zip(ends, starts, strict=True):
        between = sql[end + 1 : start].upper()
        for opener in _RUN_COMMENTS:
            if opener in between:
                raise GuardViolation(
                    "a comment the database would run is not allowed (/*!, /*M!, /*+ or --+): "
                    f"{opener}"
                )
    if tokens and tokens[-1].token_type == TokenType.SEMICOLON:
        raise GuardViolation("end the query without a semicolon: it runs as written")


def _placeholders(tokens: list[Token], tree: exp.Query) -> list[tuple[int, int, str]]:
    """Each :name in the text, as (start, end, name), in the order it appears."""
    found = []
    for colon, name in itertools.pairwise(tokens):
        adjacent = name.start == colon.end + 1
        if colon.token_type == TokenType.COLON and adjacent and name.text.isidentifier():
            found.append((colon.start, name.end + 1, name.text))
    # Every placeholder the parse found must be one placed here, or the text would carry a
    # parameter the guard did not bind.
    expected = sorted(p.name for p in tree.find_all(exp.Placeholder))
    if sorted(name for _, _, name in found) != expected:
        raise GuardViolation("the query's parameters could not be placed in its text")
    return found


def _parameter(start: int, end: int, name: str, rules: DialectRules) -> _Edit:
    marker = {"qmark": "?", "named": f":{name}", "pyformat": f"%({name})s"}[rules.param_style]
    return _Edit(start, end, marker)


def _cap(tree: exp.Query, tokens: list[Token], rules: DialectRules, max_rows: int) -> list[_Edit]:
    """Where the row cap goes in the text: nowhere if the query's own limit is within it."""
    if not isinstance(tree, exp.Select):
        raise GuardViolation(
            f"a {tree.key.upper()} cannot be capped where it stands: put it in a subquery, "
            "as SELECT * FROM (...) AS u"
        )
    existing = tree.args.get("limit")
    if existing is not None:
        count = _limit_count(existing)
        if count > max_rows:
            raise GuardViolation(
                f"the query's own row limit ({count}) is above the row cap of {max_rows}: "
                "lower it, or leave it out"
            )
        return []
    if rules.row_cap == "TOP":
        # SQL Server: TOP right after SELECT, or after its DISTINCT or ALL.
        if tree.args.get("offset") is not None:
            raise GuardViolation(
                "OFFSET without FETCH cannot be capped: add FETCH NEXT n ROWS ONLY"
            )
        at = _after_top_level_select(tokens)
        return [_Edit(at, at, f" TOP ({max_rows})")]
    # Oracle and MariaDB: at the end, right after the last token, so a trailing -- comment
    # cannot swallow it.
    clause = {"FETCH FIRST": f"FETCH FIRST {max_rows} ROWS ONLY", "LIMIT": f"LIMIT {max_rows}"}
    at = tokens[-1].end + 1
    return [_Edit(at, at, f" {clause[rules.row_cap]}")]


def _limit_count(existing: exp.Expr) -> int:
    options = existing.args.get("limit_options")
    if options is not None and (options.args.get("percent") or options.args.get("with_ties")):
        raise GuardViolation(
            f"a row limit with PERCENT or WITH TIES cannot be capped: {existing.sql()}"
        )
    count = existing.args.get("count") if isinstance(existing, exp.Fetch) else existing.expression
    if not isinstance(count, exp.Literal) or count.is_string:
        raise GuardViolation(f"a row limit must be a number: {existing.sql()}")
    return int(count.this)


def _after_top_level_select(tokens: list[Token]) -> int:
    depth = 0
    for i, token in enumerate(tokens):
        if token.token_type == TokenType.L_PAREN:
            depth += 1
        elif token.token_type == TokenType.R_PAREN:
            depth -= 1
        elif depth == 0 and token.token_type == TokenType.SELECT:
            following = tokens[i + 1] if i + 1 < len(tokens) else None
            if following is not None and following.token_type in (
                TokenType.DISTINCT,
                TokenType.ALL,
            ):
                return following.end + 1
            return token.end + 1
    raise GuardViolation("the query's SELECT could not be found in its text")


def _apply(sql: str, edits: list[_Edit], double_percent: bool) -> str:
    """The text with each edit made. PyMySQL reads % as its own: each one written is doubled."""
    pieces = []
    at = 0
    for edit in sorted(edits, key=lambda e: e.start):
        written = sql[at : edit.start]
        pieces.append(written.replace("%", "%%") if double_percent else written)
        pieces.append(edit.text)
        at = edit.end
    rest = sql[at:]
    pieces.append(rest.replace("%", "%%") if double_percent else rest)
    return "".join(pieces)


def _value(name: str, parameters: Mapping[str, object]) -> ParameterValue:
    if name not in parameters:
        raise GuardViolation(f"the query uses :{name}, which has no value")
    value = parameters[name]
    if not isinstance(value, str | int | float | Decimal | bool | date | datetime | time | None):
        raise GuardViolation(f":{name} must be a single value, not {type(value).__name__}")
    return value
