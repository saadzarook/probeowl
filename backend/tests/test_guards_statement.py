# Author: @SaadZarook
"""Guard tests. A failure here is a security bug, not a test to adjust."""

from pathlib import Path

import pytest

from probeowl.errors import GuardViolation
from probeowl.guards.dialects import MARIADB, ORACLE, SQLSERVER, DialectRules
from probeowl.guards.statement import prepare
from probeowl.model import Entity, SqlConnector
from probeowl.model.load import load_file

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
BY_NAME = {rules.name: rules for rules in (SQLSERVER, ORACLE, MARIADB)}


def refused(dialect: str, sql: str, match: str = "") -> None:
    with pytest.raises(GuardViolation, match=match or None):
        prepare(sql, {"id": "tp_91f2c7", "n": 5}, BY_NAME[dialect], max_rows=101)


# --- What passes, and how it comes out ---------------------------------------------------


def test_sqlserver_select_is_capped_with_top_and_bound_with_qmarks() -> None:
    query = prepare(
        "SELECT id, status FROM topups WHERE id = :id", {"id": "tp_91f2c7"}, SQLSERVER, 101
    )
    assert query.sql == "SELECT TOP (101) id, status FROM topups WHERE id = ? OPTION (MAXDOP 1)"
    assert query.parameters == ["tp_91f2c7"]
    assert query.guards == [
        "single SELECT",
        "row cap 101 (TOP)",
        "1 parameters bound, none interpolated",
        "runs as written: only its parameters and row cap are filled in",
    ]


def test_oracle_select_is_capped_with_fetch_first_and_bound_by_name() -> None:
    query = prepare(
        "SELECT account, direction FROM ledger_entries WHERE txn_id = :ledger_txn_id",
        {"ledger_txn_id": "lt_55e1a0", "topup_id": "unused"},
        ORACLE,
        21,
    )
    assert query.sql == (
        "SELECT account, direction FROM ledger_entries WHERE txn_id = :ledger_txn_id "
        "FETCH FIRST 21 ROWS ONLY"
    )
    assert query.parameters == {"ledger_txn_id": "lt_55e1a0"}


def test_mariadb_select_is_capped_with_limit_and_percent_signs_are_escaped() -> None:
    query = prepare(
        "SELECT card_id FROM cards WHERE visitor_profile_id = :id AND access_group LIKE 'B%'",
        {"id": "VP00012931"},
        MARIADB,
        51,
    )
    assert query.sql == (
        "SELECT card_id FROM cards WHERE visitor_profile_id = %(id)s "
        "AND access_group LIKE 'B%%' LIMIT 51"
    )
    assert query.parameters == {"id": "VP00012931"}


def test_qmark_values_follow_the_order_in_the_sql_text() -> None:
    query = prepare(
        "WITH recent AS (SELECT id FROM topups WHERE user_id = :user_id) "
        "SELECT id FROM recent WHERE id = :topup_id OR id = :user_id",
        {"topup_id": "tp_1", "user_id": "u_9"},
        SQLSERVER,
        10,
    )
    assert query.sql.count("?") == 3
    assert query.parameters == ["u_9", "tp_1", "u_9"]


@pytest.mark.parametrize(
    ("rules", "sql"),
    [
        (SQLSERVER, "SELECT TOP 5 id FROM t"),
        (SQLSERVER, "SELECT id FROM t ORDER BY id OFFSET 5 ROWS FETCH NEXT 3 ROWS ONLY"),
        (ORACLE, "SELECT id FROM t FETCH FIRST 3 ROWS ONLY"),
        (MARIADB, "SELECT id FROM t LIMIT 10, 20"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.name,
)
def test_a_row_limit_within_the_cap_is_kept_as_written(rules: DialectRules, sql: str) -> None:
    # SQL Server's MAXDOP 1 option is the only thing added.
    options = " OPTION (MAXDOP 1)" if rules is SQLSERVER else ""
    assert prepare(sql, {}, rules, 101).sql == sql + options


@pytest.mark.parametrize(
    ("rules", "sql", "count"),
    [
        (SQLSERVER, "SELECT TOP 500 id FROM t", 500),
        (ORACLE, "SELECT id FROM t FETCH FIRST 500 ROWS ONLY", 500),
        (MARIADB, "SELECT id FROM t LIMIT 1000", 1000),
    ],
    ids=lambda v: v if isinstance(v, str) else getattr(v, "name", str(v)),
)
def test_a_row_limit_above_the_cap_is_refused_not_rewritten(
    rules: DialectRules, sql: str, count: int
) -> None:
    with pytest.raises(GuardViolation) as refused:
        prepare(sql, {}, rules, 101)
    assert str(refused.value) == (
        f"the query's own row limit ({count}) is above the row cap of 101: "
        "lower it, or leave it out"
    )


@pytest.mark.parametrize(
    ("sql", "refusal"),
    [
        ("SELECT TOP 5 PERCENT id FROM t", "PERCENT or WITH TIES cannot be capped"),
        ("SELECT TOP 5 WITH TIES id FROM t ORDER BY id", "PERCENT or WITH TIES cannot be capped"),
        ("SELECT id FROM t ORDER BY id OFFSET 5 ROWS", "OFFSET without FETCH cannot be capped"),
    ],
)
def test_a_sql_server_limit_the_cap_cannot_sit_beside_is_refused(sql: str, refusal: str) -> None:
    with pytest.raises(GuardViolation, match=refusal):
        prepare(sql, {}, SQLSERVER, 101)


def test_a_union_is_refused_with_how_to_cap_it() -> None:
    with pytest.raises(GuardViolation) as refused:
        prepare("SELECT id FROM a WHERE id = :id UNION SELECT id FROM b", {"id": 1}, SQLSERVER, 7)
    assert str(refused.value) == (
        "a UNION cannot be capped where it stands: put it in a subquery, "
        "as SELECT * FROM (...) AS u"
    )
    wrapped = prepare(
        "SELECT * FROM (SELECT id FROM a WHERE id = :id UNION SELECT id FROM b) AS u",
        {"id": 1},
        SQLSERVER,
        7,
    )
    assert wrapped.sql.startswith("SELECT TOP (7) * FROM (SELECT id FROM a WHERE id = ?")


# --- It runs as written ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rules", "sql", "sent"),
    [
        # Case, spacing, line breaks and quoting are the author's, untouched.
        (
            SQLSERVER,
            "select  [Id], status\nfrom dbo.TopUps\twhere Id=:id",
            "select TOP (5)  [Id], status\nfrom dbo.TopUps\twhere Id=? OPTION (MAXDOP 1)",
        ),
        (
            SQLSERVER,
            "SELECT DISTINCT a FROM t WHERE id = :id",
            "SELECT DISTINCT TOP (5) a FROM t WHERE id = ? OPTION (MAXDOP 1)",
        ),
        # The cap goes on the outer SELECT, not the CTE's or a subquery's.
        (
            SQLSERVER,
            "WITH r AS (SELECT id FROM t) SELECT id, (SELECT 1) AS one FROM r",
            "WITH r AS (SELECT id FROM t) SELECT TOP (5) id, (SELECT 1) AS one FROM r"
            " OPTION (MAXDOP 1)",
        ),
        # A trailing comment is kept, after the cap, so it cannot swallow it.
        (
            ORACLE,
            "SELECT a FROM t WHERE id = :id -- note",
            "SELECT a FROM t WHERE id = :id FETCH FIRST 5 ROWS ONLY -- note",
        ),
        (
            MARIADB,
            "SELECT a /* why */ FROM t WHERE id = :id -- 100% sure",
            "SELECT a /* why */ FROM t WHERE id = %(id)s LIMIT 5 -- 100%% sure",
        ),
        # A :name inside a string is text, not a parameter.
        (
            SQLSERVER,
            "SELECT a FROM t WHERE note = ':id' AND id = :id",
            "SELECT TOP (5) a FROM t WHERE note = ':id' AND id = ? OPTION (MAXDOP 1)",
        ),
        # SQL Server's MAXDOP 1 goes before a trailing comment, which cannot swallow it.
        (
            SQLSERVER,
            "SELECT a FROM t WHERE id = :id -- note",
            "SELECT TOP (5) a FROM t WHERE id = ? OPTION (MAXDOP 1) -- note",
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else v.name,
)
def test_the_text_sent_is_the_text_written_with_only_parameters_and_cap(
    rules: DialectRules, sql: str, sent: str
) -> None:
    assert prepare(sql, {"id": 1}, rules, 5).sql == sent


@pytest.mark.parametrize(
    ("rules", "sql"),
    [
        (MARIADB, "SELECT a /*! , SLEEP(5) */ FROM t WHERE id = :id"),
        (MARIADB, "SELECT a /*M!100000 , SLEEP(5) */ FROM t WHERE id = :id"),
        (MARIADB, "SELECT /*+ MAX_EXECUTION_TIME(1) */ a FROM t WHERE id = :id"),
        (ORACLE, "SELECT a /*+ PARALLEL(8) */ FROM t WHERE id = :id"),
        (ORACLE, "SELECT --+ PARALLEL(8)\n a FROM t WHERE id = :id"),
        (SQLSERVER, "SELECT a FROM t WHERE id = :id /*+ hint */"),
        (MARIADB, "/*!50000 SELECT SLEEP(5) */ SELECT a FROM t WHERE id = :id"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.name,
)
def test_a_comment_the_database_would_run_is_refused(rules: DialectRules, sql: str) -> None:
    # sqlglot reads these as plain comments; the database would run them.
    with pytest.raises(GuardViolation, match=r"hint is not allowed|comment the database would run"):
        prepare(sql, {"id": 1}, rules, 5)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a FROM t WHERE note = '/*! not a comment */' AND id = :id",
        'SELECT "a--+b" FROM t WHERE id = :id',
    ],
)
def test_an_opener_inside_a_string_or_a_name_is_text(sql: str) -> None:
    assert prepare(sql, {"id": 1}, ORACLE, 5).sql.startswith(sql.split(" WHERE")[0])


@pytest.mark.parametrize("rules", [SQLSERVER, ORACLE, MARIADB], ids=lambda r: r.name)
def test_a_trailing_semicolon_is_refused_rather_than_dropped(rules: DialectRules) -> None:
    with pytest.raises(GuardViolation, match="without a semicolon"):
        prepare("SELECT a FROM t WHERE id = :id;", {"id": 1}, rules, 5)


def test_a_database_in_allowed_databases_may_be_named() -> None:
    query = prepare(
        "SELECT id FROM Wallet.dbo.topups", {}, SQLSERVER, 5, allowed_databases=["wallet"]
    )
    assert query.sql.endswith("Wallet.dbo.topups OPTION (MAXDOP 1)")
    assert prepare("SELECT 1 FROM access.cards", {}, MARIADB, 5, allowed_databases=["access"]).sql


@pytest.mark.parametrize(
    ("rules", "sql"),
    [
        (SQLSERVER, "SELECT id FROM payroll.dbo.salaries"),
        (SQLSERVER, "SELECT t.id FROM topups AS t JOIN payroll.dbo.salaries AS s ON s.id = t.id"),
        (SQLSERVER, "SELECT id FROM topups WHERE id IN (SELECT id FROM payroll.dbo.x)"),
        (SQLSERVER, "WITH p AS (SELECT id FROM [payroll].[dbo].[x]) SELECT id FROM p"),
        (MARIADB, "SELECT * FROM mysql.user"),
        (MARIADB, "SELECT * FROM information_schema.tables"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.name,
)
def test_a_database_outside_allowed_databases_is_refused(rules: DialectRules, sql: str) -> None:
    with pytest.raises(GuardViolation, match=r"names database .*allowed_databases"):
        prepare(sql, {}, rules, 5, allowed_databases=["wallet", "access"])


def test_with_no_allowed_databases_any_named_database_is_refused() -> None:
    with pytest.raises(GuardViolation, match="this binding allows: none"):
        prepare("SELECT id FROM wallet.dbo.topups", {}, SQLSERVER, 5)


def test_unqualified_and_schema_qualified_names_need_no_allowed_database() -> None:
    assert prepare("SELECT id FROM dbo.topups", {}, SQLSERVER, 5).sql.endswith(
        "dbo.topups OPTION (MAXDOP 1)"
    )
    # Oracle qualifies by schema, never by database.
    query = prepare("SELECT 1 FROM ledger.ledger_entries", {}, ORACLE, 5)
    assert "ledger.ledger_entries" in query.sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM topups OPTION (MAXDOP 64)",
        "SELECT id FROM topups OPTION (RECOMPILE)",
        "WITH r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) SELECT n FROM r "
        "OPTION (MAXRECURSION 0)",
    ],
)
def test_query_hints_are_refused(sql: str) -> None:
    with pytest.raises(GuardViolation, match="a query hint"):
        prepare(sql, {}, SQLSERVER, 5)


def test_names_give_the_parameter_behind_each_qmark() -> None:
    sql = "SELECT 1 FROM t WHERE a = :b AND c = :a AND d = :b"
    assert prepare(sql, {"a": 1, "b": 2}, SQLSERVER, 5).names == ["b", "a", "b"]
    assert prepare(sql, {"a": 1, "b": 2}, ORACLE, 5).names == ["a", "b"]


def test_text_that_looks_like_a_parameter_inside_a_string_is_not_one() -> None:
    query = prepare("SELECT id FROM t WHERE note = 'ratio 1:id'", {}, SQLSERVER, 10)
    assert query.parameters == []
    assert "'ratio 1:id'" in query.sql


# --- Forbidden statements -----------------------------------------------------------------

FORBIDDEN = [
    # Anything that is not a single SELECT
    ("sqlserver", "UPDATE topups SET status = 'failed' WHERE id = :id", "only a SELECT"),
    ("sqlserver", "DELETE FROM topups WHERE id = :id", "only a SELECT"),
    ("sqlserver", "INSERT INTO topups (id) VALUES (:id)", "only a SELECT"),
    ("sqlserver", "MERGE topups AS t USING src AS s ON t.id = s.id WHEN MATCHED THEN DELETE;", ""),
    ("sqlserver", "TRUNCATE TABLE topups", ""),
    ("sqlserver", "DROP TABLE topups", "only a SELECT"),
    ("sqlserver", "CREATE TABLE x (id INT)", "only a SELECT"),
    ("sqlserver", "SELECT id FROM topups; DROP TABLE topups", "single statement"),
    ("sqlserver", "SELECT 1; SELECT 2", "single statement"),
    ("sqlserver", "", "single statement"),
    ("sqlserver", "BEGIN TRAN", ""),
    ("sqlserver", "SET LOCK_TIMEOUT -1", ""),
    ("sqlserver", "EXEC xp_cmdshell 'dir'", ""),
    ("sqlserver", "WAITFOR DELAY '00:00:05'", "could not be parsed"),
    # SQL Server: hints, temp tables, reaching outside the database
    ("sqlserver", "SELECT * FROM topups WITH (NOLOCK) WHERE id = :id", "table hint"),
    ("sqlserver", "SELECT * FROM topups WITH (READUNCOMMITTED)", "table hint"),
    ("sqlserver", "SELECT * FROM topups WITH (HOLDLOCK)", "table hint"),
    ("sqlserver", "SELECT * FROM topups WITH (UPDLOCK)", "table hint"),
    ("sqlserver", "SELECT * FROM topups WITH (TABLOCKX)", "table hint"),
    ("sqlserver", "SELECT * INTO #tmp FROM topups", "SELECT INTO"),
    ("sqlserver", "SELECT * FROM OPENROWSET('SQLNCLI', 'srv', 'SELECT 1')", "OPENROWSET"),
    ("sqlserver", "SELECT * FROM OPENQUERY(srv, 'SELECT 1')", "OPENQUERY"),
    ("sqlserver", "SELECT @@version", "session variables|:name parameters"),
    ("sqlserver", "SELECT * FROM topups WHERE id = @id", ":name parameters"),
    ("sqlserver", "SELECT * FROM topups WHERE id = ?", "named parameters"),
    ("sqlserver", "SELECT TOP (:n) * FROM topups", "row limit must be a number"),
    # Oracle: locks, PL/SQL, package calls
    ("oracle", "SELECT * FROM ledger_entries FOR UPDATE", "locking clause"),
    ("oracle", "SELECT * FROM ledger_entries WHERE txn_id = :id FOR UPDATE NOWAIT", "locking"),
    ("oracle", "LOCK TABLE ledger_entries IN EXCLUSIVE MODE", ""),
    ("oracle", "BEGIN DELETE FROM ledger_entries; END;", ""),
    ("oracle", "DECLARE x NUMBER; BEGIN x := 1; END;", ""),
    ("oracle", "SELECT dbms_lock.sleep(5) FROM dual", "package calls"),
    ("oracle", "SELECT utl_http.request('http://x') FROM dual", "package calls"),
    ("oracle", "SELECT * FROM t; DELETE FROM t", "single statement"),
    # MariaDB: locks, delays, file access
    ("mariadb", "SELECT * FROM cards FOR UPDATE", "locking clause"),
    ("mariadb", "SELECT * FROM cards LOCK IN SHARE MODE", "locking clause"),
    ("mariadb", "SELECT GET_LOCK('probe', 10)", "GET_LOCK"),
    ("mariadb", "SELECT SLEEP(5)", "SLEEP"),
    ("mariadb", "SELECT BENCHMARK(1000000, MD5('x'))", "BENCHMARK"),
    ("mariadb", "SELECT LOAD_FILE('/etc/passwd')", "LOAD_FILE"),
    ("mariadb", "SELECT * FROM cards INTO OUTFILE '/tmp/cards'", ""),
    ("mariadb", "SELECT card_id INTO @v FROM cards", ""),
    # Reaching another database through this one
    ("oracle", "SELECT * FROM t@remote_db", "database links"),
    (
        "oracle",
        "SELECT * FROM ledger.ledger_entries@remote_db WHERE txn_id = :id",
        "database links",
    ),
    ("oracle", "SELECT * FROM t WHERE x IN (SELECT y FROM u@remote_db)", "database links"),
    ("oracle", 'SELECT a FROM t, u@"Remote" WHERE t.id = u.id', "database links"),
    ("oracle", "SELECT dummy FROM dual@remote", "database links"),
    ("oracle", "WITH r AS (SELECT * FROM t@remote_db) SELECT * FROM r", "database links"),
    ("sqlserver", "SELECT * FROM srv.wallet.dbo.topups", "linked servers"),
    ("sqlserver", "SELECT * FROM [srv].[wallet].[dbo].[topups]", "linked servers"),
    # MariaDB writing files: INTO OUTFILE and INTO DUMPFILE, in either position
    ("mariadb", "SELECT * INTO OUTFILE '/tmp/cards' FROM cards", ""),
    ("mariadb", "SELECT * FROM cards INTO DUMPFILE '/tmp/cards'", ""),
    ("mariadb", "SELECT * INTO DUMPFILE '/tmp/cards' FROM cards", ""),
    ("mariadb", "SELECT card_id FROM cards WHERE id = :id INTO OUTFILE '/tmp/c'", ""),
    # SQL Server delays and procedure calls, also behind a CTE
    ("sqlserver", "WAITFOR TIME '23:00'", ""),
    ("sqlserver", "SELECT id FROM topups; WAITFOR DELAY '00:00:05'", ""),
    ("sqlserver", "WITH x AS (SELECT 1 AS a) SELECT a FROM x; WAITFOR DELAY '00:00:05'", ""),
    ("sqlserver", "EXEC sp_who", ""),
    ("sqlserver", "EXECUTE sp_configure", ""),
    ("sqlserver", "EXEC sp_executesql N'SELECT 1'", ""),
    ("sqlserver", "EXECUTE('SELECT 1')", ""),
    ("sqlserver", "EXEC('DELETE FROM topups')", ""),
    ("sqlserver", "WITH x AS (SELECT 1 AS a) EXEC sp_who", ""),
    ("sqlserver", "WITH x AS (SELECT 1 AS a) EXECUTE('SELECT 1')", ""),
    ("sqlserver", "WITH x AS (EXEC sp_who) SELECT * FROM x", ""),
    ("sqlserver", "WITH x AS (SELECT 1 AS a) SELECT a FROM x; EXEC sp_who", "single statement"),
    ("sqlserver", "SELECT * FROM OPENQUERY(srv, 'EXEC sp_who')", "OPENQUERY"),
]


@pytest.mark.parametrize(("dialect", "sql", "match"), FORBIDDEN, ids=[f[1][:50] for f in FORBIDDEN])
def test_forbidden_statement_is_refused(dialect: str, sql: str, match: str) -> None:
    refused(dialect, sql, match)


def test_a_data_change_inside_a_cte_is_refused() -> None:
    refused(
        "sqlserver", "WITH d AS (SELECT id FROM t) DELETE FROM t WHERE id IN (SELECT id FROM d)"
    )


def test_nul_in_the_sql_is_refused() -> None:
    refused("sqlserver", "SELECT id FROM t WHERE id = :id\x00", "NUL")


# --- Injection attempts through IDs ---------------------------------------------------------

HOSTILE_IDS = [
    "tp_91f2c7' OR '1'='1",
    "tp_91f2c7'; DROP TABLE topups; --",
    "' UNION SELECT password FROM users --",
    "tp_91f2c7\x00 OR 1=1",
    "1); EXEC xp_cmdshell('dir'); --",
    ":other_param",
    "%(id)s",
    "?",
    "x" * 10_000,
]


@pytest.mark.parametrize("rules", [SQLSERVER, ORACLE, MARIADB], ids=lambda r: r.name)
@pytest.mark.parametrize("hostile", HOSTILE_IDS, ids=range(len(HOSTILE_IDS)))
def test_an_id_is_always_a_bound_value_never_sql(rules: DialectRules, hostile: str) -> None:
    sql = "SELECT id, status FROM topups WHERE id = :id"
    expected = prepare(sql, {"id": "tp_91f2c7"}, rules, 10).sql
    query = prepare(sql, {"id": hostile}, rules, 10)
    # The SQL text never changes with the value, and the value reaches the driver whole.
    assert query.sql == expected
    bound = (
        query.parameters if isinstance(query.parameters, list) else list(query.parameters.values())
    )
    assert bound == [hostile]


@pytest.mark.parametrize("value", [["a", "b"], {"a": 1}, object()], ids=["list", "dict", "object"])
def test_a_parameter_must_be_a_single_value(value: object) -> None:
    with pytest.raises(GuardViolation, match="must be a single value"):
        prepare("SELECT id FROM t WHERE id = :id", {"id": value}, SQLSERVER, 10)


def test_a_parameter_without_a_value_is_refused() -> None:
    with pytest.raises(GuardViolation, match=":ledger_txn_id, which has no value"):
        prepare("SELECT 1 FROM t WHERE txn_id = :ledger_txn_id", {}, ORACLE, 10)


def test_the_row_cap_must_be_positive() -> None:
    with pytest.raises(GuardViolation, match="at least 1"):
        prepare("SELECT 1 FROM t", {}, SQLSERVER, 0)


# --- The examples' lookups pass ---------------------------------------------------------------


def _example_lookups() -> list[tuple[str, str, DialectRules]]:
    lookups = []
    for app in ("wallet", "card-access"):
        connectors = {}
        for path in (EXAMPLES / app / "connectors").glob("*.yaml"):
            [connector] = load_file(path)
            if isinstance(connector, SqlConnector):
                connectors[connector.connector] = BY_NAME[connector.type]
        for path in (EXAMPLES / app / "entities").glob("*.yaml"):
            [entity] = load_file(path)
            assert isinstance(entity, Entity)
            for name, location in entity.locations.items():
                if location.using in connectors:
                    lookups.append(
                        (f"{entity.entity}.{name}", location.lookup, connectors[location.using])
                    )
    return lookups


@pytest.mark.parametrize(
    ("name", "sql", "rules"),
    _example_lookups(),
    ids=lambda v: v if isinstance(v, str) and "." in v and " " not in v else "",
)
def test_every_example_sql_lookup_passes(name: str, sql: str, rules: DialectRules) -> None:
    keys = {
        "topup_id": "tp_91f2c7",
        "provider_payment_id": "pay_7hq2m9",
        "ledger_txn_id": "lt_55e1a0",
        "visitor_profile_id": "VP00012931",
    }
    query = prepare(sql, keys, rules, 51)
    assert query.guards[0] == "single SELECT"


@pytest.mark.parametrize(
    ("rules", "sql"),
    [
        (ORACLE, "SELECT /*+ PARALLEL(8) */ COUNT(*) FROM ledger_entries"),
        (ORACLE, "SELECT /*+ parallel */ account FROM ledger_entries WHERE txn_id = :id"),
        (ORACLE, "SELECT a FROM (SELECT /*+ PARALLEL(t 4) */ a FROM t)"),
        (ORACLE, "SELECT /*+ INDEX(t ix_t) FULL(u) */ a FROM t, u"),
        (MARIADB, "SELECT /*+ MAX_EXECUTION_TIME(100000) */ card_id FROM cards"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.name,
)
def test_optimizer_hints_are_refused(rules: DialectRules, sql: str) -> None:
    with pytest.raises(GuardViolation, match="an optimizer hint"):
        prepare(sql, {"id": "x"}, rules, 5)


@pytest.mark.parametrize(
    ("rules", "sql"),
    [
        (ORACLE, "SELECT a /* plain */ FROM t WHERE id = :id"),
        (ORACLE, "SELECT a FROM t -- trailing\nWHERE id = :id"),
        (ORACLE, "SELECT a /*+ PARALLEL(8) */ FROM t WHERE id = :id"),
        (SQLSERVER, "SELECT a FROM t WHERE id = :id -- note"),
        (MARIADB, "SELECT a FROM t /* x */ WHERE id = :id"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.name,
)
def test_a_plain_comment_runs_as_written_and_a_hint_never_does(
    rules: DialectRules, sql: str
) -> None:
    # A plain comment is sent as written; one that carries a hint is refused.
    if "/*+" in sql:
        with pytest.raises(GuardViolation, match="comment the database would run"):
            prepare(sql, {"id": 1}, rules, 5)
        return
    sent = prepare(sql, {"id": 1}, rules, 5).sql
    assert ("/*" in sent) or ("--" in sent)
    assert "PARALLEL" not in sent.upper()


# --- What the Connectors screen lists as Forbidden ----------------------------------------

# One statement for each item listed, so the screen can never promise what the guard does not do.
FORBIDDEN_EXAMPLES = {
    "sqlserver": {
        "NOLOCK": "SELECT id FROM t WITH (NOLOCK)",
        "HOLDLOCK": "SELECT id FROM t WITH (HOLDLOCK)",
        "TABLOCKX": "SELECT id FROM t WITH (TABLOCKX)",
        "UPDLOCK": "SELECT id FROM t WITH (UPDLOCK)",
        "temp tables": "SELECT id FROM #t",
        "OPENROWSET()": "SELECT * FROM OPENROWSET('a', 'b', 'c')",
        "OPENQUERY()": "SELECT * FROM OPENQUERY(srv, 'SELECT 1')",
        "OPENDATASOURCE()": "SELECT OPENDATASOURCE('a', 'b')",
        "OPENXML()": "SELECT * FROM OPENXML(1, 'a')",
        "linked servers": "SELECT id FROM srv.db.dbo.t",
    },
    "oracle": {
        "FOR UPDATE": "SELECT id FROM t FOR UPDATE",
        "LOCK TABLE": "LOCK TABLE t IN EXCLUSIVE MODE",
        "PL/SQL blocks": "BEGIN NULL; END;",
        "package calls": "SELECT dbms_lock.sleep(1) FROM dual",
        "database links": "SELECT id FROM t@remote",
    },
    "mariadb": {
        "FOR UPDATE": "SELECT id FROM t FOR UPDATE",
        "LOCK IN SHARE MODE": "SELECT id FROM t LOCK IN SHARE MODE",
        "GET_LOCK()": "SELECT GET_LOCK('a', 1)",
        "SLEEP()": "SELECT SLEEP(1)",
        "BENCHMARK()": "SELECT BENCHMARK(1, 1)",
        "LOAD_FILE()": "SELECT LOAD_FILE('x')",
    },
}


@pytest.mark.parametrize("dialect", BY_NAME)
def test_every_item_shown_as_forbidden_has_a_refused_example(dialect: str) -> None:
    assert set(BY_NAME[dialect].forbidden_shown) == set(FORBIDDEN_EXAMPLES[dialect])


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        (dialect, sql)
        for dialect, examples in FORBIDDEN_EXAMPLES.items()
        for sql in examples.values()
    ],
)
def test_what_the_screen_shows_as_forbidden_is_refused(dialect: str, sql: str) -> None:
    refused(dialect, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM #t",
        "SELECT id FROM ##t",
        "SELECT id FROM dbo.#t",
        "SELECT id FROM [#t]",
        "SELECT a.id FROM orders a JOIN #t b ON a.id = b.id",
        "SELECT id FROM orders WHERE id IN (SELECT id FROM ##t)",
    ],
)
def test_temp_tables_are_refused(sql: str) -> None:
    refused("sqlserver", sql, "temp tables are not allowed")


def test_a_cte_is_not_mistaken_for_a_temp_table() -> None:
    query = prepare("WITH t AS (SELECT id FROM orders) SELECT id FROM t", {}, SQLSERVER, 10)
    assert "orders" in query.sql
