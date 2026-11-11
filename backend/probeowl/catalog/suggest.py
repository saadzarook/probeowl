# Author: @SaadZarook
"""Suggest a table's label and column roles from its name and shape. A person confirms each.

The rules follow docs/design.md, "Table catalog": an _audit or _history suffix, created_at
plus status columns, links to other systems' keys, very small row counts. Every table gets a
suggestion, with the reasons, so nothing is labelled silently. A column named for another
imported table's key, such as cards.visitor_profile_id for visitor_profile.id, is suggested as a
link to it: the entity wizard follows the same rule.
"""

import re
from collections.abc import Iterable, Sequence
from typing import NamedTuple

from probeowl.model.catalog import ColumnRole, LinkTarget, TableLabel
from probeowl.model.review import LabelSuggestion
from probeowl.model.schema import ColumnSchema, TableSchema

# Small enough to be configuration rather than records.
REFERENCE_ROWS = 1000

_TIME_NAMES = (
    "created_at",
    "created_on",
    "inserted_at",
    "occurred_at",
    "received_at",
    "posted_at",
    "changed_at",
    "logged_at",
    "event_time",
    "timestamp",
)
_TIME_TYPES = ("date", "time")  # date, datetime, datetime2, timestamp, timestamptz
_STATUS_NAMES = ("status", "state")
_PII = re.compile(
    r"(^|_)(e_?mail|phone|mobile|first_name|last_name|full_name|address|birth|dob|ssn|"
    r"national_id|passport|iban|card_number|pan)($|_)",
    re.IGNORECASE,
)
_EXTERNAL_KEY = re.compile(r"(^|_)(provider|external|partner|vendor)_\w*id$", re.IGNORECASE)
_AUDIT = re.compile(r"_(audit|history|hist|changes)$", re.IGNORECASE)
_LOG = re.compile(r"(_log|_logs|webhooks?|_deliveries|notifications?)$", re.IGNORECASE)
_OUTBOX = re.compile(r"(outbox|_queue)", re.IGNORECASE)
_MAPPING = re.compile(r"(_ids|_mapping|_map|_xref)$", re.IGNORECASE)
# One current row per subject: a created_at and a status on such a table describe the subject,
# they do not make its rows pile up like transactions.
_MASTER = re.compile(
    r"(^|_)(profiles?|users?|customers?|accounts?|members?|persons?|people|employees?|parties|"
    r"party|merchants?|products?|visitors?|contacts?|companies|company|organi[sz]ations?)($|_)",
    re.IGNORECASE,
)


class KeyTable(NamedTuple):
    """An imported table with a one-column primary key: what a column named for it can link to."""

    connector: str
    table: str
    key: str


def key_tables(tables: Iterable[tuple[str, TableSchema]]) -> list[KeyTable]:
    """The tables, given with their connectors, whose primary key is a single column."""
    return [
        KeyTable(connector, t.table, t.primary_key[0])
        for connector, t in tables
        if len(t.primary_key) == 1
    ]


def suggest(
    table: TableSchema, connector: str | None = None, keys: Sequence[KeyTable] = ()
) -> LabelSuggestion:
    """The likeliest label for the table, its reasons, and roles for its notable columns.

    connector and keys: the table's connector and the application's imported tables, for the
    links its columns' names suggest.
    """
    links = _links(table, connector, keys)
    columns = _roles(table)
    for column in links:
        columns.setdefault(column, []).append(ColumnRole.LINK)
    time = next((c for c, roles in columns.items() if ColumnRole.TIME in roles), None)
    status = next((c for c, roles in columns.items() if ColumnRole.STATUS in roles), None)
    external = [c for c, roles in columns.items() if ColumnRole.EXTERNAL_KEY in roles]
    label, reasons = _label(table, time, status, external)
    return LabelSuggestion(label=label, reasons=reasons, columns=columns, links=links)


def _links(
    table: TableSchema, connector: str | None, keys: Sequence[KeyTable]
) -> dict[str, LinkTarget]:
    """Each column named <table>_<key> or <singular>_<key> for exactly one other table's key.

    A table on the same connector wins over one elsewhere; two candidates at the same distance
    are a guess, so none is suggested.
    """
    others = [k for k in keys if (k.connector, k.table.lower()) != (connector, table.table.lower())]
    links = {}
    for column in table.columns:
        name = column.name.lower()
        if _EXTERNAL_KEY.search(name):
            continue  # another system's key, not a table's
        found = [k for k in others if name in _key_names(k)]
        near = [k for k in found if k.connector == connector] or found
        if len(near) == 1:
            links[column.name] = LinkTarget(
                connector=near[0].connector, table=near[0].table, column=near[0].key
            )
    return links


def _key_names(target: KeyTable) -> set[str]:
    name, key = target.table.lower(), target.key.lower()
    return {f"{name}_{key}", f"{_singular(name)}_{key}"}


def _singular(name: str) -> str:
    # As the wizard's: topups -> topup, categories -> category, address stays.
    if name.endswith("ies"):
        return f"{name[:-3]}y"
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def _label(
    table: TableSchema, time: str | None, status: str | None, external: list[str]
) -> tuple[TableLabel, list[str]]:
    name = table.table
    if audit := _AUDIT.search(name):
        return TableLabel.AUDIT, [f"name ends in {audit.group(0)}"]
    if _OUTBOX.search(name):
        return TableLabel.OUTBOX, ["name says outbox or queue"]
    if _LOG.search(name):
        return TableLabel.LOG, ["name says log, webhook, delivery or notification"]
    if external and (_MAPPING.search(name) or len(table.columns) <= 4):
        return TableLabel.EXTERNAL_MAPPING, [f"holds another system's key ({', '.join(external)})"]
    if master := _MASTER.search(name):
        word = master.group(2).lower()
        why = f"name says {word}: one current record per subject"
        return TableLabel.MASTER, [why + (f", whatever its {time} column" if time else "")]
    if time is not None and status is not None:
        return TableLabel.TRANSACTION, [f"{time} and {status} columns"]
    if table.approx_rows is not None and table.approx_rows <= REFERENCE_ROWS and time is None:
        rows = "1 row" if table.approx_rows == 1 else f"{table.approx_rows} rows"
        return TableLabel.REFERENCE, [f"about {rows} and no time column"]
    if time is not None:
        return TableLabel.TRANSACTION, [f"{time} column: rows accumulate over time"]
    return TableLabel.MASTER, ["no time column: keyed lookups of current records"]


def _roles(table: TableSchema) -> dict[str, list[ColumnRole]]:
    roles: dict[str, list[ColumnRole]] = {}

    def add(column: str, role: ColumnRole) -> None:
        roles.setdefault(column, []).append(role)

    for key in table.primary_key:
        add(key, ColumnRole.PRIMARY_KEY)
    time = _time_column(table.columns)
    if time is not None:
        add(time, ColumnRole.TIME)
    for column in table.columns:
        if column.name.lower() in _STATUS_NAMES:
            add(column.name, ColumnRole.STATUS)
        if _PII.search(column.name):
            add(column.name, ColumnRole.PII)
        if _EXTERNAL_KEY.search(column.name):
            add(column.name, ColumnRole.EXTERNAL_KEY)
    return roles


def _time_column(columns: list[ColumnSchema]) -> str | None:
    # A known name first, in the order above; else the first date or time column.
    by_name = {c.name.lower(): c.name for c in columns}
    for name in _TIME_NAMES:
        if name in by_name:
            return by_name[name]
    return next((c.name for c in columns if any(t in c.type.lower() for t in _TIME_TYPES)), None)
