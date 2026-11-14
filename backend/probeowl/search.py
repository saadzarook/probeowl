# Author: @SaadZarook
"""Global search (#38): names and titles in the loaded configuration, imported tables and columns,
and runs by id or subject. Read-only, and never anything a run read.

What it matches is fixed here, field by field. It never looks at a lookup's SQL, a runbook step's
text, evidence, location results, expectation values, or a connector's host, URL, login or
keychain name. A run is found by its id, or by its subject or identifier as stored, and nothing
masked is matched or shown: a PII identifier is stored as [masked], which matches nothing, and
the subject of an entity whose root key is PII is neither matched nor shown. Only identifiers
that aren't PII match by a prefix of 4 or more characters; otherwise a run matches exactly.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, Engine, func, or_, select

from probeowl.catalog.review import build_review
from probeowl.engine.masking import MASKED
from probeowl.errors import RequestError, unknown_application
from probeowl.model.check import LoadedApplication
from probeowl.model.schema import SchemaSnapshot
from probeowl.model.search import SearchGroup, SearchItem, SearchKind, SearchResults
from probeowl.store.schemas import latest_snapshots
from probeowl.store.tables import LOCAL_WORKSPACE, runs

MAX_QUERY = 128
MIN_PREFIX = 4  # a run id or an identifier that isn't PII matches from this many characters
RUN_CANDIDATES = 200  # the newest rows the store gives before the PII rules apply
KIND_TITLES = {"inspect": "Inspect", "validate": "Validate", "health": "Health"}


@dataclass(frozen=True)
class _RunRow:
    """The run columns search reads, and no other: never its results or evidence."""

    id: str
    application: str
    entity: str
    identifier: str
    identifier_value: str
    subject: str | None
    status: str
    probe: str
    started_at: datetime


@dataclass(frozen=True)
class _Match:
    rank: int
    key: str
    item: SearchItem


def _rank(query: str, name: str, title: str = "", also: Iterable[str] = ()) -> int | None:
    """Prefix before substring, the name before the title; None when nothing matches."""
    name, title = name.casefold(), title.casefold()
    if name.startswith(query):
        return 0
    if title.startswith(query):
        return 1
    if query in name:
        return 2
    if query in title:
        return 3
    if any(query in other.casefold() for other in also):
        return 4
    return None


def search(
    applications: dict[str, LoadedApplication],
    store: Engine,
    q: str,
    application: str | None = None,
    limit: int = 8,
    workspace_id: str = LOCAL_WORKSPACE,
) -> SearchResults:
    """The groups that match q, each best first and cut at limit, in SearchKind's order."""
    query = q.strip()
    if not 1 <= len(query) <= MAX_QUERY:
        raise RequestError(
            f"give 1 to {MAX_QUERY} characters to search for", {"q": "1 to 128 characters"}
        )
    if application is not None and application not in applications:
        raise unknown_application(application, applications)
    scope = [applications[application]] if application else list(applications.values())
    folded = query.casefold()
    found: dict[SearchKind, list[_Match]] = {kind: [] for kind in SearchKind}
    for app in scope:
        for kind, matches in _configuration(app, folded):
            found[kind].extend(matches)
    with store.connect() as conn:
        for app in scope:
            snapshots = latest_snapshots(conn, workspace_id, app.application.application)
            for kind, matches in _tables_and_columns(app, snapshots, folded):
                found[kind].extend(matches)
        groups = [_group(kind, matches, limit) for kind, matches in found.items() if matches]
        run_group = _runs(conn, workspace_id, scope, folded, limit)
    if run_group is not None:
        groups.append(run_group)
    return SearchResults(query=query, groups=groups)


def _group(kind: SearchKind, matches: list[_Match], limit: int) -> SearchGroup:
    best = sorted(matches, key=lambda m: (m.rank, m.key))
    return SearchGroup(
        kind=kind, items=[m.item for m in best[:limit]], more=max(0, len(best) - limit)
    )


def _configuration(app: LoadedApplication, q: str) -> Iterable[tuple[SearchKind, list[_Match]]]:
    name = app.application.application
    title = app.application.title
    yield (
        SearchKind.APPLICATION,
        _one(
            _rank(q, name, title),
            SearchItem(
                kind=SearchKind.APPLICATION,
                application=None,
                id=name,
                title=title,
                detail=app.application.owner,
            ),
        ),
    )
    entities, locations = [], []
    for entity in app.entities.values():
        entities += _one(
            _rank(q, entity.entity, entity.title),
            SearchItem(
                kind=SearchKind.ENTITY,
                application=name,
                id=entity.entity,
                title=entity.title or entity.entity,
                detail=entity.root_key,
            ),
        )
        for location_name, location in entity.locations.items():
            locations += _one(
                _rank(q, location_name, location.title),
                SearchItem(
                    kind=SearchKind.LOCATION,
                    application=name,
                    id=f"{entity.entity}.{location_name}",
                    title=location.title or location_name,
                    detail=f"{entity.entity} · {location.using}",
                ),
            )
    yield SearchKind.ENTITY, entities
    yield SearchKind.LOCATION, locations
    yield (
        SearchKind.PROBE,
        [
            match
            for probe in app.probes.values()
            for match in _one(
                _rank(q, probe.probe, probe.title),
                SearchItem(
                    kind=SearchKind.PROBE,
                    application=name,
                    id=probe.probe,
                    title=probe.title or probe.probe,
                    detail=f"{KIND_TITLES.get(probe.kind, probe.kind)} · {probe.entity}",
                ),
            )
        ],
    )
    yield (
        SearchKind.CONNECTOR,
        [
            match
            for connector in app.connectors.values()
            for match in _one(
                _rank(q, connector.connector),
                SearchItem(
                    kind=SearchKind.CONNECTOR,
                    application=name,
                    id=connector.connector,
                    title=connector.connector,
                    detail=f"{connector.type} · {', '.join(connector.environments)}",
                ),
            )
        ],
    )
    yield (
        SearchKind.RUNBOOK,
        [
            match
            for runbook in app.runbooks.values()
            for match in _one(
                # Its symptoms too: ledger.missing is found by "ledger missing" or "missing".
                _rank(q, runbook.runbook, runbook.title, _symptoms(runbook.applies_to.when)),
                SearchItem(
                    kind=SearchKind.RUNBOOK,
                    application=name,
                    id=runbook.runbook,
                    title=runbook.title,
                    detail=runbook.applies_to.entity,
                ),
            )
        ],
    )


def _symptoms(when: list[str]) -> list[str]:
    return [
        f"{location} {outcome.replace('_', ' ')}"
        for location, _, outcome in (w.partition(".") for w in when)
    ]


def _tables_and_columns(
    app: LoadedApplication, snapshots: list[SchemaSnapshot], q: str
) -> Iterable[tuple[SearchKind, list[_Match]]]:
    name = app.application.application
    review = build_review(app, snapshots)
    roles = {
        (entry.connector, entry.table.lower(), column.lower()): [str(r) for r in marked.roles]
        for entry in (app.catalog.tables if app.catalog else [])
        for column, marked in entry.columns.items()
    }
    tables = []
    for table in review.tables:
        if table.label is not None:
            labels = [str(table.label)]
        elif table.suggestion is not None:
            labels = [str(table.suggestion.label), "suggested"]
        else:
            labels = []
        tables += _one(
            _rank(q, table.table),
            SearchItem(
                kind=SearchKind.TABLE,
                application=name,
                id=f"{table.connector}.{table.table}",
                title=table.table,
                detail=table.connector,
                labels=labels,
            ),
        )
    yield SearchKind.TABLE, tables
    seen: set[tuple[str, str, str]] = set()
    columns = []
    for snapshot in snapshots:
        for imported in snapshot.tables:
            for column in imported.columns:
                key = (snapshot.connector, imported.table.lower(), column.name.lower())
                if key in seen:  # the same column imported in another environment
                    continue
                seen.add(key)
                columns += _one(
                    _rank(q, column.name),
                    SearchItem(
                        kind=SearchKind.COLUMN,
                        application=name,
                        id=f"{snapshot.connector}.{imported.table}.{column.name}",
                        title=column.name,
                        detail=f"{imported.table} · {column.type}",
                        labels=roles.get(key, []),
                    ),
                )
    yield SearchKind.COLUMN, columns


def _one(rank: int | None, item: SearchItem) -> list[_Match]:
    return [] if rank is None else [_Match(rank, item.id.casefold(), item)]


def _runs(
    conn: Connection, workspace_id: str, scope: list[LoadedApplication], q: str, limit: int
) -> SearchGroup | None:
    """Runs by id, subject or identifier, newest first, under the PII rules above."""
    r = runs.c
    names = [app.application.application for app in scope]
    exact = [func.lower(r.subject) == q, func.lower(r.identifier_value) == q]
    prefix = []
    if len(q) >= MIN_PREFIX:
        pattern = _escaped(q) + "%"
        prefix = [
            func.lower(column).like(pattern, escape="\\")
            for column in (r.id, r.subject, r.identifier_value)
        ]
    rows = [_RunRow(*row) for row in conn.execute(
        select(
            r.id, r.application, r.entity, r.identifier, r.identifier_value, r.subject,
            r.status, r.probe, r.started_at,
        )
        .where(r.workspace_id == workspace_id, r.application.in_(names))
        .where(or_(*exact, *prefix))
        .order_by(r.started_at.desc(), r.id.desc())
        .limit(RUN_CANDIDATES)
    ).all()]  # fmt: skip
    apps = {app.application.application: app for app in scope}
    matched = [row for row in rows if _run_matches(row, q, apps.get(row.application))]
    if not matched:
        return None
    return SearchGroup(
        kind=SearchKind.RUN,
        items=[_run_item(row, apps.get(row.application)) for row in matched[:limit]],
        more=max(0, len(matched) - limit),
    )


def _escaped(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _root_is_pii(app: LoadedApplication | None, entity: str) -> bool:
    # An entity no longer loaded: its subject is treated as PII, the safe way.
    found = app.entities.get(entity) if app else None
    return found is None or found.identifiers[found.root_key].pii


def _identifier_is_pii(app: LoadedApplication | None, entity: str, identifier: str) -> bool:
    found = app.entities.get(entity) if app else None
    known = found.identifiers.get(identifier) if found else None
    return known is None or known.pii


def _run_matches(row: _RunRow, q: str, app: LoadedApplication | None) -> bool:
    run_id, subject, value = row.id, row.subject, row.identifier_value
    entity, identifier = row.entity, row.identifier
    if len(q) >= MIN_PREFIX and run_id.casefold().startswith(q):
        return True
    shown: list[tuple[str, Callable[[], bool]]] = []
    if subject:
        shown.append((subject, lambda: _root_is_pii(app, entity)))
    if value and value != MASKED:
        shown.append((value, lambda: _identifier_is_pii(app, entity, identifier)))
    for text, is_pii in shown:
        if is_pii():
            continue  # never matched: neither whole nor by part
        folded = text.casefold()
        if folded == q or (len(q) >= MIN_PREFIX and folded.startswith(q)):
            return True
    return False


def _run_item(row: _RunRow, app: LoadedApplication | None) -> SearchItem:
    subject = row.subject
    if subject and _root_is_pii(app, row.entity):
        subject = MASKED
    probe = app.probes.get(row.probe) if app else None
    return SearchItem(
        kind=SearchKind.RUN,
        application=row.application,
        id=row.id,
        title=subject or row.identifier_value,
        detail=" · ".join(
            [
                str(row.status).capitalize(),
                row.started_at.strftime("%d %b %H:%M"),
                probe.probe if probe else row.probe,
            ]
        ),
    )
