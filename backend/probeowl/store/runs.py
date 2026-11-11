# Author: @SaadZarook
"""Store and read runs, with their location results, evidence and expectation results."""

from datetime import datetime
from typing import Any

from sqlalchemy import Connection, and_, delete, insert, or_, select, update

from probeowl.errors import NotFoundError
from probeowl.model.run import (
    DRAFT_VERSION_PREFIX,
    Actor,
    Evidence,
    ExpectationEvaluation,
    HealthyAttempt,
    InvestigationRecord,
    LocationResult,
    Match,
    Run,
    RunSummary,
)
from probeowl.store.tables import evidence, expectation_results, location_results, runs


def save_run(conn: Connection, workspace_id: str, run: Run) -> None:
    """Store a run's current state. Called again as outcomes arrive and when it ends."""
    key = and_(runs.c.workspace_id == workspace_id, runs.c.id == run.id)
    row = _run_row(run)
    if conn.execute(select(runs.c.id).where(key)).first() is None:
        conn.execute(insert(runs).values(workspace_id=workspace_id, id=run.id, **row))
    else:
        conn.execute(update(runs).where(key).values(**row))
    # Children are rewritten whole: a running run only ever gains results, and this keeps
    # the order the engine reported them in.
    for table in (evidence, location_results, expectation_results):
        conn.execute(
            delete(table).where(table.c.workspace_id == workspace_id, table.c.run_id == run.id)
        )
    ids = {"workspace_id": workspace_id, "run_id": run.id}
    for seq, result in enumerate(run.locations):
        conn.execute(insert(location_results).values(**ids, seq=seq, **_location_row(result)))
        if result.evidence is not None:
            conn.execute(insert(evidence).values(**ids, seq=seq, **_evidence_row(result.evidence)))
    for seq, evaluation in enumerate(run.expectations):
        conn.execute(
            insert(expectation_results).values(**ids, seq=seq, **evaluation.model_dump(mode="json"))
        )


def get_run(conn: Connection, workspace_id: str, run_id: str) -> Run:
    """Read a run; NotFoundError if this workspace has no run with that id."""
    row = conn.execute(
        select(runs).where(runs.c.workspace_id == workspace_id, runs.c.id == run_id)
    ).first()
    if row is None:
        raise NotFoundError(f"no run with id {run_id}")
    child = {"workspace_id": workspace_id, "run_id": run_id}
    evidence_by_seq = {
        e.seq: e for e in conn.execute(select(evidence).filter_by(**child)).mappings()
    }
    locations = [
        LocationResult(
            location=r.location,
            parent=r.parent,
            row=r.row_index,
            outcome=r.outcome,
            reason=r.reason,
            caveat=r.caveat,
            evidence=_evidence(evidence_by_seq[r.seq]) if r.seq in evidence_by_seq else None,
        )
        for r in conn.execute(
            select(location_results).filter_by(**child).order_by(location_results.c.seq)
        )
    ]
    expectations = [
        ExpectationEvaluation(
            step=r.step,
            expectation=r.expectation,
            result=r.result,
            detail=r.detail,
            caveat=r.caveat,
        )
        for r in conn.execute(
            select(expectation_results).filter_by(**child).order_by(expectation_results.c.seq)
        )
    ]
    return Run(
        id=row.id,
        application=row.application,
        environment=row.environment,
        probe=row.probe,
        kind=row.kind,
        probe_version=row.probe_version,
        entity=row.entity,
        identifier=row.identifier,
        identifier_value=row.identifier_value,
        subject=row.subject,
        via=row.via,
        resolution=None if row.resolution is None else LocationResult(**row.resolution),
        matches=[Match(**match) for match in row.matches or []],
        from_run=row.from_run,
        healthy_for=row.healthy_for,
        healthy_attempt=(
            None if row.healthy_attempt is None else HealthyAttempt(**row.healthy_attempt)
        ),
        warnings=row.warnings or [],
        caveat=row.caveat,
        status=row.status,
        skip_reason=row.skip_reason,
        stop_reason=row.stop_reason,
        actor=Actor(kind=row.actor_kind, name=row.actor_name, on_behalf_of=row.actor_on_behalf_of),
        investigation=(
            None if row.investigation is None else InvestigationRecord(**row.investigation)
        ),
        started_at=row.started_at,
        finished_at=row.finished_at,
        locations=locations,
        expectations=expectations,
    )


def _run_row(run: Run) -> dict[str, Any]:
    return {
        "application": run.application,
        "environment": run.environment,
        "probe": run.probe,
        "kind": run.kind.value,
        "probe_version": run.probe_version,
        "entity": run.entity,
        "identifier": run.identifier,
        "identifier_value": run.identifier_value,
        "subject": run.subject,
        "via": run.via,
        "resolution": None if run.resolution is None else run.resolution.model_dump(mode="json"),
        "matches": [match.model_dump(mode="json") for match in run.matches] or None,
        "from_run": run.from_run,
        "healthy_for": run.healthy_for,
        "healthy_attempt": (
            None if run.healthy_attempt is None else run.healthy_attempt.model_dump(mode="json")
        ),
        "warnings": run.warnings or None,
        "status": run.status.value,
        "skip_reason": run.skip_reason,
        "stop_reason": run.stop_reason,
        "caveat": run.caveat,
        "actor_kind": run.actor.kind.value,
        "actor_name": run.actor.name,
        "actor_on_behalf_of": run.actor.on_behalf_of,
        "investigation": (
            None if run.investigation is None else run.investigation.model_dump(mode="json")
        ),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _location_row(result: LocationResult) -> dict[str, Any]:
    return {
        "location": result.location,
        "parent": result.parent,
        "row_index": result.row,
        "outcome": result.outcome.value,
        "reason": result.reason,
        "caveat": result.caveat,
    }


def _evidence_row(item: Evidence) -> dict[str, Any]:
    return {
        "connector": item.connector,
        "query_text": item.query,
        "parameters": item.parameters,
        "guards": item.guards,
        "result_rows": item.rows,
        "column_types": item.column_types or None,
        "rows_total": item.rows_total,
        "started_at": item.started_at,
        "duration_ms": item.duration_ms,
    }


def _evidence(row: Any) -> Evidence:
    return Evidence(
        connector=row["connector"],
        query=row["query_text"],
        parameters=row["parameters"],
        guards=row["guards"],
        rows=row["result_rows"],
        column_types=row["column_types"] or {},
        rows_total=row["rows_total"],
        started_at=row["started_at"],
        duration_ms=row["duration_ms"],
    )


def running_run_ids(conn: Connection, workspace_id: str) -> list[str]:
    """The runs this workspace still records as running."""
    return list(
        conn.execute(
            select(runs.c.id).where(runs.c.workspace_id == workspace_id, runs.c.status == "running")
        ).scalars()
    )


def list_runs(
    conn: Connection,
    workspace_id: str,
    application: str,
    environment: str,
    before: tuple[datetime, str] | None,
    limit: int,
) -> list[RunSummary]:
    """Runs in one application and environment, newest first, starting after before.

    Healthy-comparison candidates are left out: each is shown on the run it was compared with.
    before is (started_at, id) of the last run already listed; the id breaks ties.
    """
    query = select(runs).where(
        runs.c.workspace_id == workspace_id,
        runs.c.application == application,
        runs.c.environment == environment,
        runs.c.healthy_for.is_(None),
    )
    if before is not None:
        at, run_id = before
        query = query.where(
            or_(runs.c.started_at < at, and_(runs.c.started_at == at, runs.c.id < run_id))
        )
    query = query.order_by(runs.c.started_at.desc(), runs.c.id.desc()).limit(limit)
    return [_summary(row) for row in conn.execute(query)]


def _summary(row: Any) -> RunSummary:
    investigation = row.investigation or {}
    return RunSummary(
        id=row.id,
        application=row.application,
        environment=row.environment,
        probe=row.probe,
        kind=row.kind,
        draft=row.probe_version.startswith(DRAFT_VERSION_PREFIX),
        identifier=row.identifier,
        identifier_value=row.identifier_value,
        subject=row.subject,
        status=row.status,
        stop_reason=row.stop_reason,
        skip_reason=row.skip_reason,
        caveat=row.caveat,
        actor=row.actor_name,
        reason=investigation.get("reason"),
        reference=investigation.get("reference"),
        started_at=row.started_at,
        finished_at=row.finished_at,
    )
