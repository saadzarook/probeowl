# Connectors: what a read login needs

A probe login reads, and nothing else. This page lists what it needs on each database, and the one
grant that is optional.

## Replica lag (optional)

Before a run's first lookup on a replica binding, ProbeOwl reads how far that replica is behind its
primary. The query is fixed, reads replication state and never data, and needs one grant beyond
reading. The grant is optional: ProbeOwl runs without it. Without it:

- lag is always **unknown**, and the Connectors screen, the run's evidence and its warning say
  `replica lag unknown: missing <grant>`, with the grant the database asked for;
- the circuit breaker cannot pause the connector for lag, so it never trips for a replica that has
  fallen behind;
- a location cannot be **Stale**: a record that has not reached the replica yet shows as Missing,
  and in Production each such run carries a warning that Missing results cannot be confirmed fresh,
  and each such Missing, the expectations that read it and the run itself carry a caveat ("replica
  lag unknown, the record may not have arrived yet"). The status and exit code stay as they are.

| Database | Lag query | Grant, as the database administrator |
| --- | --- | --- |
| SQL Server | `sys.dm_hadr_database_replica_states` for this database | `GRANT VIEW DATABASE PERFORMANCE STATE TO <user>;` in the probed database, on SQL Server 2022 and 2025: both refuse naming it (seen in CI on 2022, and natively on 2025, 17.0.1000.7). On 2019 and earlier the view needs `VIEW SERVER STATE`, on the server; not yet seen on a real 2019. ProbeOwl names the permission SQL Server itself refused. |
| MariaDB | `SHOW REPLICA STATUS` | `GRANT REPLICA MONITOR ON *.* TO '<user>'@'<host>';` (MariaDB 10.5.9 and later, where `SLAVE MONITOR` is the same privilege, so either grant works). ProbeOwl names the privilege MariaDB refused, and MariaDB 11.8 refuses naming `SLAVE MONITOR` (seen natively on 11.8.9), so that is what the screen shows there. Before 10.5 the grant is `REPLICATION CLIENT`; not yet seen on a real server. |
| Oracle | `V$DATAGUARD_STATS`, `apply lag` | `GRANT SELECT ON SYS.V_$DATAGUARD_STATS TO <user>;` (the grant is on the view; `V$DATAGUARD_STATS` is its public synonym). Oracle's refusal names no grant, so ProbeOwl names this one. |

A test on a real SQL Server reads lag as a login without the grant, then grants exactly the
permission ProbeOwl named and reads it again, to show the name is the one that is enough.

## Runbook steps on Oracle's `v$` views

A runbook read step can read one of Oracle's dynamic performance views, as the example's
`check-locks` step reads `v$session`. Like the lag query, it needs a grant beyond reading tables, on
the view itself: `v$session` is the public synonym of `SYS.V_$SESSION`.

| Step reads | Grant, as the database administrator |
| --- | --- |
| `v$session` | `GRANT SELECT ON SYS.V_$SESSION TO <user>;` |
| any other `v$name` or `gv$name` | `GRANT SELECT ON SYS.V_$NAME TO <user>;` (or `SYS.GV_$NAME`) |

Give it to the Production binding's login only if its steps should run there. Without it, Oracle
refuses the view by name, and ProbeOwl names the grant: when the step is run, its plan check finds the
refusal and the step is copy only, with the reason `<connector>'s login in <environment> is missing
SELECT on SYS.V_$SESSION, which a DBA grants`. Confirming a run without the plan check does not
change that. A read that fails the same way any other time is Error, `missing SELECT on
SYS.V_$SESSION`, never a bare refusal.

## Time offsets

A time with an offset (SQL Server `datetimeoffset`, Oracle `TIMESTAMP WITH TIME ZONE`) is never
compared with a column that stores none (`datetime2`, `datetime`, `date`, a plain `TIMESTAMP`,
any MariaDB time). SQL Server reads such a column as UTC to compare them, and Oracle reads it in
the session's time zone, so a +04:00 value matches rows 4 hours off when the column holds local
time. ProbeOwl cannot know which the column holds, so:

- **Saving** refuses a lookup that compares a parameter taken from a column with an offset (by
  `key_from`, a fan-out row or a healthy sample parameter) with a column without one, unless the
  SQL converts the parameter: `CAST(:at AS datetime2)` for the value's own clock time, or
  `CAST(:at AT TIME ZONE '<zone>' AS datetime2)`. `AT TIME ZONE`, `SWITCHOFFSET` or `DATEADD`
  alone keep the offset and are not enough (seen on SQL Server 2025). On Oracle,
  `CAST(:at AT TIME ZONE '<zone>' AS TIMESTAMP)` for the column's local time, or
  `SYS_EXTRACT_UTC(:at)` when it holds UTC. A plain `CAST(:at AS TIMESTAMP)` gives the UTC time,
  because the driver sends a time with an offset as its UTC instant.
- **A run** refuses a value typed with an offset, such as `2026-11-07T14:00+04:00`, when it is
  compared with a column without one: "this column stores time without an offset; enter it without
  +04:00, in the column's own time". SQL Server would otherwise drop the offset of the text.

The other way round is refused too. A time without an offset against a column with one is read
as UTC by SQL Server, and in the session's time zone by Oracle:

- **Saving** needs the lookup to give the value its offset: `TODATETIMEOFFSET(:at, '+04:00')` or
  `:at AT TIME ZONE '<zone>'` on SQL Server, `FROM_TZ(:at, '+04:00')` on Oracle. `CAST` to
  `datetimeoffset`, `SWITCHOFFSET` and `DATEADD` are not enough: the value is still read as UTC
  (seen on SQL Server 2025). On Oracle, `AT TIME ZONE` first reads the value in the session's time
  zone, so it is not accepted either.
- **A run** refuses a time typed without an offset, such as `2026-11-07T14:00`, against such a
  column: "this column stores time with an offset; enter the time with its offset, such as
  2026-11-07T14:00+04:00".

Column types come from the latest schema import. With no import, saving warns for a connector
whose catalog time column a lookup compares, and a run refuses a typed time rather than guess.
`owl check` reads no imports, so it does not run this check, and says so for each such connector:
`warning: <application>: time-offset check not run: no schema import for <connector>`.

How each database's driver handles a time on the way, checked on real servers:

- **SQL Server** has no session time zone. A time with an offset is sent as a `datetimeoffset`
  and read back with its offset.
- **Oracle** (python-oracledb, thin mode) would set each session's time zone from the machine
  running ProbeOwl. ProbeOwl sets it instead, on every connection: to the binding's `time_zone`
  (an offset such as `+04:00`), or the database's own (`DBTIMEZONE`). That is what
  `CURRENT_DATE`, `CURRENT_TIMESTAMP` and `LOCALTIMESTAMP` return in, and how a `TIMESTAMP` is
  compared with a time with an offset. Evidence says it (`TIME_ZONE +00:00 (the database's own)`),
  and so does the Connectors screen. A time with an offset is sent as its UTC instant, because the
  driver would send 14:00 +04:00 as 14:00 +00:00. The driver cannot read a
  `TIMESTAMP WITH TIME ZONE` with its offset, so a lookup that selects one as it is gives Error,
  and so does saving it: select `SYS_EXTRACT_UTC(col)` for its UTC instant, or
  `TO_CHAR(col, 'YYYY-MM-DD HH24:MI:SS TZH:TZM')`.
- **MariaDB** has no type with an offset; a session's `time_zone` is the server's own. PyMySQL
  would send a time with an offset as its clock time, the offset dropped, so a lookup that does not
  convert it, for example `CAST(:at AS DATETIME)`, gives Error: the same rule the save check
  applies, for a lookup it never saw.

## What runs

A lookup runs exactly as written. The statement guard checks the parsed query, then fills in
only two things, at the positions of their tokens in the text:

- each `:name` parameter, as the driver's marker (`?` on SQL Server, `%(name)s` on MariaDB, where
  each literal `%` is doubled for the driver; Oracle keeps `:name`);
- the row cap: `TOP (n)` right after the top-level `SELECT` (or its `DISTINCT`) on SQL Server, so
  the server keeps its row goal; `FETCH FIRST n ROWS ONLY` on Oracle and `LIMIT n` on MariaDB,
  right after the last token. SQL Server's `OPTION (MAXDOP 1)` goes there too, so a trailing
  `--` comment cannot swallow either. The adapter also stops reading after one more row than
  allowed.

Evidence shows that exact text. Comments are sent as written. The guard refuses what it cannot
fill in without changing the query's meaning: a comment the database would run (`/*!`, `/*M!`,
`/*+`, `--+`), a `UNION`, `INTERSECT` or `EXCEPT` at the top (put it in a subquery), a row limit
of the query's own above the cap, `TOP ... PERCENT` or `WITH TIES`, SQL Server's `OFFSET` without
`FETCH`, and a trailing semicolon. A location reads one row unless its cardinality is many, so a
`TOP 5` on a `one` location is refused at save and at run time alike.
