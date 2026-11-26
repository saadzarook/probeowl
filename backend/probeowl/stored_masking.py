# Author: @SaadZarook
"""A run as it may be stored: every PII identifier masked, the subject too when the key is PII.

Each location's rows are masked as the runner masks them now: its pii and mask fields, and for a
REST location every field it does not expose.

RunService passes each run through masked_run before saving it, so the store, and the API, audit
and CLI that read it, never hold the value (#66). `owl store mask-pii` applies the same to runs
stored before this, with the current configuration.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import quote, unquote

from pydantic import JsonValue
from sqlalchemy import Connection, select, update

from probeowl.engine.masking import (
    MASKED,
    Masked,
    key_is_pii,
    mask_keys,
    mask_rows,
    mask_text,
    mask_values,
    masked_fields,
    pii_fields,
)
from probeowl.model import Run
from probeowl.model.check import LoadedApplication
from probeowl.model.entity import Entity, LocationBase
from probeowl.model.run import Evidence, LocationResult
from probeowl.model.runbook_step import StepResult
from probeowl.store.runs import get_run, save_run
from probeowl.store.tables import audit_events, runbook_steps, runs


def masked_run(run: Run, entity: Entity) -> Run:
    """The run with each PII identifier masked wherever it shows, and its subject if the key is."""
    hidden = pii_fields(entity)
    key = key_is_pii(entity)
    locations, values, unexposed = _masked_locations(run.locations, entity, hidden)
    identifier = entity.identifiers.get(run.identifier)
    return run.model_copy(
        update={
            "subject": MASKED if key and run.subject is not None else run.subject,
            # Stored as given when the identifier was not yet PII: masked now that it is.
            "identifier_value": MASKED
            if identifier is not None and identifier.pii
            else run.identifier_value,
            "matches": [
                m.model_copy(
                    update={
                        "subject": MASKED if key else m.subject,
                        "fields": _masked_row(m.fields, hidden),
                    }
                )
                for m in run.matches
            ],
            "resolution": run.resolution
            and run.resolution.model_copy(
                update={"evidence": _masked_evidence(run.resolution.evidence, hidden)}
            ),
            "locations": locations,
            "expectations": [
                e.model_copy(
                    update={"detail": e.detail and mask_text(e.detail, values | unexposed)}
                )
                for e in run.expectations
            ],
            "healthy_attempt": run.healthy_attempt
            and run.healthy_attempt.model_copy(
                update={
                    "sample": _masked_evidence(run.healthy_attempt.sample, hidden),
                    "replaced_subject": MASKED
                    if key and run.healthy_attempt.replaced_subject is not None
                    else run.healthy_attempt.replaced_subject,
                }
            ),
        }
    )


def _masked_locations(
    results: list[LocationResult], entity: Entity, hidden: set[str]
) -> tuple[list[LocationResult], set[str], set[str]]:
    """The results with their evidence masked, the values a stored request line gave away, and
    the values of REST fields the runner masks now, which a failed expectation's detail may show.
    """
    given: set[str] = set()
    unexposed: set[str] = set()
    masked = []
    for result in results:
        location = _location(entity, result)
        evidence = _masked_evidence(result.evidence, hidden)
        if evidence is not None and location is not None:
            fields = masked_fields(entity, location)
            unexposed |= _unexposed_values(evidence.rows, fields)
            evidence = evidence.model_copy(update={"rows": mask_rows(evidence.rows, fields)})
            evidence, values = _masked_request(evidence, _templates(location))
            given |= values
        masked.append(result.model_copy(update={"evidence": evidence}))
    if not given:
        return masked, given, unexposed
    # Any location may hold the key in a field of another name, and an error could have named it.
    return (
        [
            result.model_copy(
                update={
                    "reason": result.reason and mask_text(result.reason, given),
                    "evidence": result.evidence and _masked_echo(result.evidence, given),
                }
            )
            for result in masked
        ],
        given,
        unexposed,
    )


def _unexposed_values(rows: list[dict[str, JsonValue]], fields: Masked) -> set[str]:
    """The values held by the fields a REST location does not expose, as text, nested ones too."""
    if fields.shown is None:
        return set()
    found: set[str] = set()
    for row in rows:
        for key, value in row.items():
            if key.lower() not in fields.shown:
                found |= _scalars(value)
    return found - {MASKED, ""}


def _scalars(value: JsonValue) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_scalars(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_scalars(item) for item in value))
    # A boolean or a null says nothing about whom a record is about.
    if value is None or isinstance(value, bool):
        return set()
    return {str(value)}


def _masked_echo(evidence: Evidence, values: set[str]) -> Evidence:
    return evidence.model_copy(
        update={
            "rows": mask_values(evidence.rows, values),
            "column_types": mask_keys(evidence.column_types, values),
        }
    )


def _is_request(evidence: Evidence) -> bool:
    return evidence.query.startswith("GET ")


def _location(entity: Entity, result: LocationResult) -> LocationBase | None:
    """The location a stored result came from, as the configuration has it now."""
    if result.parent is not None:
        parent = entity.locations.get(result.parent)
        return parent.each.get(result.location) if parent is not None else None
    return entity.locations.get(result.location)


def _templates(location: LocationBase) -> list[str]:
    """The lookups the location has now: what its stored request line was probably made from."""
    return [each for each in (location.lookup, location.fallback) if each is not None]


_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _masked_request(evidence: Evidence, templates: Iterable[str]) -> tuple[Evidence, set[str]]:
    """Evidence whose request line shows no value its parameters mask, and the values it showed.

    Runs stored before 0.2.4 kept them in the path and query. A line none of the location's
    lookups could have sent has nothing to say which part is the value: all of it is masked.
    """
    hidden = {name for name, value in evidence.parameters.items() if value == MASKED}
    if not hidden or not _is_request(evidence):
        return evidence, set()
    for template in templates:
        names = _PLACEHOLDER.findall(template)
        found = _as_sent(template).fullmatch(evidence.query)
        # The keys too: a line the fallback sent can look like the lookup's.
        if found is None or set(names) != set(evidence.parameters):
            continue
        line, values, at = "", set(), 0
        for index, name in enumerate(names, start=1):
            start, end = found.span(index)
            if name in hidden:
                line += evidence.query[at:start] + MASKED
                values.add(unquote(found.group(index).replace("+", " ")))
                values.add(unquote(found.group(index)))
            else:
                line += evidence.query[at:end]
            at = end
        line += evidence.query[at:]
        return evidence.model_copy(update={"query": line}), values - {MASKED, ""}
    return evidence.model_copy(update={"query": f"GET {MASKED}"}), set()


def _as_sent(template: str) -> re.Pattern[str]:
    """What a lookup's request line looks like: any base path, then the template's own parts."""
    target = template.strip().partition(" ")[2].strip()
    parts = _PLACEHOLDER.split(target)
    # Every other part is a placeholder's name. A value is one path segment, or one query value.
    pattern = "".join(
        r"([^/?&#]*)" if index % 2 else re.escape(quote(part, safe="/:@!$&'()*+,;=-._~%?"))
        for index, part in enumerate(parts)
    )
    return re.compile(rf"GET (?:/[^?]*)?{pattern}")


def masked_step(result: StepResult, entity: Entity) -> StepResult:
    """A runbook step's result with each PII identifier masked in its rows and parameters."""
    return result.model_copy(
        update={"evidence": _masked_evidence(result.evidence, pii_fields(entity))}
    )


def _masked_evidence(evidence: Evidence | None, hidden: set[str]) -> Evidence | None:
    if evidence is None or not hidden:
        return evidence
    return evidence.model_copy(
        update={
            "parameters": {
                name: MASKED if name.lower() in hidden and value is not None else value
                for name, value in evidence.parameters.items()
            },
            "rows": [_masked_row(row, hidden) for row in evidence.rows],
        }
    )


def _masked_row(row: dict[str, JsonValue], hidden: set[str]) -> dict[str, JsonValue]:
    if not hidden:
        return row
    masked: dict[str, JsonValue] = mask_rows([row], hidden)[0]
    return masked


@dataclass(frozen=True)
class MaskedRun:
    """One stored run `owl store mask-pii` changes: what it masks, never the values."""

    run_id: str
    fields: list[str]
    audit_events: int = 0  # its audit events that still hold a value: append-only, so kept


def mask_stored_runs(
    conn: Connection,
    workspace_id: str,
    applications: dict[str, LoadedApplication],
    apply: bool,
) -> tuple[list[MaskedRun], int]:
    """Each stored run the current configuration would mask more, and how many were skipped.

    apply: write the masked run and its runbook steps' results. A run of an entity that is not
    loaded is skipped and counted: with no configuration, nothing says what is PII.
    """
    changed: list[MaskedRun] = []
    skipped = 0
    ids = conn.execute(
        select(runs.c.id).where(runs.c.workspace_id == workspace_id).order_by(runs.c.started_at)
    ).scalars()
    for run_id in list(ids):
        run = get_run(conn, workspace_id, run_id)
        app = applications.get(run.application)
        entity = app.entities.get(run.entity) if app is not None else None
        if entity is None:
            skipped += 1
            continue
        masked = masked_run(run, entity)
        steps = _masked_steps(conn, workspace_id, run_id, entity)
        fields = _what_changed(run, masked) + [f"runbook step {s}" for s, _, _ in steps]
        if not fields:
            continue
        changed.append(
            MaskedRun(run_id, fields, _audit_holding(conn, workspace_id, run_id, entity))
        )
        if apply:
            save_run(conn, workspace_id, masked)
            for step, runbook, result in steps:
                conn.execute(
                    update(runbook_steps)
                    .where(
                        runbook_steps.c.workspace_id == workspace_id,
                        runbook_steps.c.run_id == run_id,
                        runbook_steps.c.runbook == runbook,
                        runbook_steps.c.step == step,
                    )
                    .values(result=result.model_dump(mode="json"))
                )
    return changed, skipped


def _audit_holding(conn: Connection, workspace_id: str, run_id: str, entity: Entity) -> int:
    """How many of the run's audit events hold a value masking now hides: counted, not changed.

    The audit is append-only, enforced by the store's guard, so they are left as they are and
    the person is told how many there are.
    """
    key = key_is_pii(entity)
    pii = {name for name, identifier in entity.identifiers.items() if identifier.pii}
    rows = conn.execute(
        select(audit_events.c.details).where(
            audit_events.c.workspace_id == workspace_id, audit_events.c.run_id == run_id
        )
    ).scalars()
    held = 0
    for details in rows:
        identifiers = details.get("identifiers")
        if (
            (key and details.get("subject") not in (None, MASKED))
            or (key and any(m != MASKED for m in details.get("matches") or []))
            or (
                isinstance(identifiers, dict)
                and any(name in pii and v != MASKED for name, v in identifiers.items())
            )
        ):
            held += 1
    return held


def _masked_steps(
    conn: Connection, workspace_id: str, run_id: str, entity: Entity
) -> list[tuple[str, str, StepResult]]:
    """(step, runbook, masked result) for each saved step result that masking changes."""
    rows = conn.execute(
        select(runbook_steps).where(
            runbook_steps.c.workspace_id == workspace_id, runbook_steps.c.run_id == run_id
        )
    )
    found = []
    for row in rows:
        if row.result is None:
            continue
        result = StepResult(**row.result)
        masked = masked_step(result, entity)
        if masked != result:
            found.append((row.step, row.runbook, masked))
    return found


def _what_changed(run: Run, masked: Run) -> list[str]:
    names = []
    if run.subject != masked.subject:
        names.append("subject")
    if run.identifier_value != masked.identifier_value:
        names.append("identifier")
    if run.matches != masked.matches:
        names.append("matches")
    if run.resolution != masked.resolution:
        names.append("resolution evidence")
    changed = [a.location for a, b in zip(run.locations, masked.locations, strict=True) if a != b]
    names += [f"{location} evidence" for location in changed]
    if run.expectations != masked.expectations:
        names.append("expectations")
    if run.healthy_attempt != masked.healthy_attempt:
        names.append("healthy comparison")
    return names
