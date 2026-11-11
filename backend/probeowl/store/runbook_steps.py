# Author: @SaadZarook
"""A runbook's steps in one run: a read query's latest result and a manual step's tick."""

from sqlalchemy import Connection, and_, insert, select, update

from probeowl.model.runbook_step import StepResult
from probeowl.store.tables import runbook_steps


def step_states(
    conn: Connection, workspace_id: str, run_id: str, runbook: str
) -> dict[str, tuple[bool, StepResult | None]]:
    """Each step with a saved state: whether it is ticked, and its latest result."""
    rows = conn.execute(
        select(runbook_steps).where(
            runbook_steps.c.workspace_id == workspace_id,
            runbook_steps.c.run_id == run_id,
            runbook_steps.c.runbook == runbook,
        )
    )
    return {
        row.step: (bool(row.done), None if row.result is None else StepResult(**row.result))
        for row in rows
    }


def save_step(
    conn: Connection,
    workspace_id: str,
    run_id: str,
    runbook: str,
    step: str,
    done: bool = False,
    result: StepResult | None = None,
) -> None:
    """Keep a step's tick or its latest result. Earlier ones stay in the audit."""
    values = {
        "done": done,
        "result": None if result is None else result.model_dump(mode="json"),
    }
    key = and_(
        runbook_steps.c.workspace_id == workspace_id,
        runbook_steps.c.run_id == run_id,
        runbook_steps.c.runbook == runbook,
        runbook_steps.c.step == step,
    )
    if conn.execute(select(runbook_steps.c.step).where(key)).first() is None:
        conn.execute(
            insert(runbook_steps).values(
                workspace_id=workspace_id, run_id=run_id, runbook=runbook, step=step, **values
            )
        )
    else:
        conn.execute(update(runbook_steps).where(key).values(**values))
