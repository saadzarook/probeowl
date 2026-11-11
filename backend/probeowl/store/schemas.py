# Author: @SaadZarook
"""Store and read imported schema snapshots."""

import uuid

from sqlalchemy import Connection, Row, and_, func, insert, select

from probeowl.errors import NotFoundError
from probeowl.model.schema import SchemaSnapshot, TableSchema
from probeowl.store.tables import schema_snapshots


def save_snapshot(conn: Connection, workspace_id: str, snapshot: SchemaSnapshot) -> None:
    """Store one import. Earlier ones stay, so drift can be traced back."""
    conn.execute(
        insert(schema_snapshots).values(
            workspace_id=workspace_id,
            id=str(uuid.uuid4()),
            application=snapshot.application,
            connector=snapshot.connector,
            environment=snapshot.environment,
            imported_at=snapshot.imported_at,
            tables=[t.model_dump(mode="json", by_alias=True) for t in snapshot.tables],
            truncated=snapshot.truncated,
        )
    )


def latest_snapshot(
    conn: Connection, workspace_id: str, application: str, connector: str, environment: str
) -> SchemaSnapshot:
    """The most recent import; NotFoundError if there has been none."""
    s = schema_snapshots.c
    row = conn.execute(
        select(schema_snapshots)
        .where(
            s.workspace_id == workspace_id,
            s.application == application,
            s.connector == connector,
            s.environment == environment,
        )
        .order_by(s.imported_at.desc())
        .limit(1)
    ).first()
    if row is None:
        raise NotFoundError(
            f"{connector} has not been imported in {environment}; "
            f"run: owl catalog import {connector} --application {application} --env {environment}"
        )
    return _snapshot(row)


def latest_snapshots(conn: Connection, workspace_id: str, application: str) -> list[SchemaSnapshot]:
    """The most recent import of each connector in each environment of an application."""
    s = schema_snapshots.c
    newest = (
        select(s.connector, s.environment, func.max(s.imported_at).label("imported_at"))
        .where(s.workspace_id == workspace_id, s.application == application)
        .group_by(s.connector, s.environment)
        .subquery()
    )
    rows = conn.execute(
        select(schema_snapshots)
        .join(
            newest,
            and_(
                s.connector == newest.c.connector,
                s.environment == newest.c.environment,
                s.imported_at == newest.c.imported_at,
            ),
        )
        .where(s.workspace_id == workspace_id, s.application == application)
    )
    return [_snapshot(row) for row in rows]


def _snapshot(row: Row) -> SchemaSnapshot:
    return SchemaSnapshot(
        application=row.application,
        connector=row.connector,
        environment=row.environment,
        imported_at=row.imported_at,
        tables=[TableSchema.model_validate(t) for t in row.tables],
        truncated=row.truncated,
    )
