# Author: @SaadZarook
"""Request and response bodies that exist only in the API."""

from typing import Annotated, Literal

from pydantic import Field

from probeowl.model.catalog import CatalogTable
from probeowl.model.common import FieldName, Name, ProbeOwlModel
from probeowl.model.kill_switch import KillTarget
from probeowl.model.probe import ProbeKind
from probeowl.model.run import InvestigationRecord, RunStatus, RunSummary
from probeowl.model.runbook import (
    ExternalQueryStep,
    ManualStep,
    ProbeStep,
    ReadQueryStep,
    RunbookBase,
    WriteQueryStep,
)
from probeowl.model.save_check import SaveCheckReport


class FieldError(ProbeOwlModel):
    """One field of a request that was not accepted."""

    field: str = Field(description="The field, dotted: investigation.reference.")
    message: str


class Problem(ProbeOwlModel):
    """An error, as RFC 9457 problem details. Never contains a stack trace."""

    type: str = Field(default="about:blank", description="A URI that identifies the problem type.")
    title: str
    status: int = Field(ge=400, le=599)
    detail: str | None = Field(default=None, description="What happened, for a person to act on.")
    instance: str | None = Field(default=None, description="The request path.")
    errors: list[FieldError] | None = Field(
        default=None, description="For a 422: each field that was not accepted, and why."
    )


class EnvironmentSummary(ProbeOwlModel):
    """One environment of an application."""

    name: Name
    title: str
    production: bool


class ApplicationSummary(ProbeOwlModel):
    """An application as listed in the application selector."""

    name: Name
    title: str
    owner: str
    environments: list[EnvironmentSummary]


class ProbeSummary(ProbeOwlModel):
    """A probe as listed for an entity."""

    name: Name
    title: str = Field(description="As in the probe's file; omitted there: its name, spaced.")
    kind: ProbeKind
    entity: Name
    owner: str
    description: str | None = None


class StartRun(ProbeOwlModel):
    """Start an inspect or validate run. The probe decides which."""

    application: Name
    environment: Name
    probe: Name
    value: str = Field(min_length=1, description="The ID entered, such as tp_91f2c7.")
    identifier: FieldName | None = Field(
        default=None, description="Which identifier the value is. Omitted: detected from patterns."
    )
    investigation: InvestigationRecord = Field(
        description="Reason always; reference too when the environment is Production."
    )


class RunList(ProbeOwlModel):
    """A page of runs, newest first."""

    runs: list[RunSummary]
    next: str | None = Field(
        default=None, description="Pass as before to list the next, older page. Null: no more."
    )


class RunStarted(ProbeOwlModel):
    """A run that has started. Poll GET /api/runs/{id} until it is no longer running."""

    id: str
    status: Literal[RunStatus.RUNNING]


MAX_PICKED_MATCHES = 5


class StartHealthyComparison(ProbeOwlModel):
    """How to find the known-good subject. Neither field: the newest that passes."""

    narrow: Name | None = Field(default=None, description="A closer match from the entity's list.")
    subject: str | None = Field(
        default=None, min_length=1, description="A known-good ID a person pasted."
    )


class RunRunbookStep(ProbeOwlModel):
    """Run a step, named in the path. It carries no SQL and no values: the server has both."""

    run_without_plan_check: bool = Field(
        default=False,
        description=(
            "In Production, the person's acknowledgement that the step's plan could not be read. "
            "Recorded in the audit with the actor."
        ),
    )


class TickRunbookStep(ProbeOwlModel):
    """A manual step's tick."""

    done: bool


class PickMatches(ProbeOwlModel):
    """Subjects picked from an Ambiguous run's matches, to run its probe for."""

    subjects: list[str] = Field(
        min_length=1,
        max_length=MAX_PICKED_MATCHES,
        description="Root key values from the run's matches; up to 5 run together.",
    )


class ImportSchema(ProbeOwlModel):
    """Import one SQL connector's schema in one environment."""

    connector: Name
    environment: Name


class ConfirmTable(ProbeOwlModel):
    """A table's catalog entry to confirm. Without base: preview it. With it: write it."""

    entry: CatalogTable
    base: str | None = Field(
        default=None,
        description=(
            "The base of the preview being confirmed. Omitted: nothing is written and the "
            "change is returned. Given: written only if catalog.yaml is still as it was then."
        ),
    )


class CatalogChange(ProbeOwlModel):
    """A change to a YAML file in the folder owl serve loaded. Never committed or pushed."""

    path: str = Field(description="Relative to the folder owl serve loaded.")
    diff: str = Field(description="Unified diff of the change.")
    base: str = Field(description="sha256 of the file before the change, or 'absent'.")
    after: str = Field(description="sha256 of the file after the change.")
    written: bool


class SetKillSwitch(KillTarget):
    """Turn one kill switch on or off, and say why: both are audited with the actor."""

    reason: str = Field(min_length=1, description="Why. Recorded in the audit.")
    reference: str | None = Field(
        default=None, max_length=64, description="Jira key, support case or change ID."
    )


class YamlFile(ProbeOwlModel):
    """A probe's or an entity's file as it is on disk, comments included, for the editor."""

    path: str = Field(description="Relative to the folder owl serve loaded.")
    text: str = Field(description="The file's whole text, unchanged.")


class SaveProbe(ProbeOwlModel):
    """A probe to save. Without base: preview it. With it: write it."""

    probe: str = Field(min_length=1, description="The probe's YAML, as in the editor.")
    entity: str | None = Field(
        default=None,
        min_length=1,
        description="The entity's YAML, when it was edited with the probe. Both or neither are "
        "written.",
    )
    environment: Name = Field(description="Where plans are read for the index checks.")
    base: str | None = Field(
        default=None,
        description=(
            "The base of the preview being confirmed. Omitted: nothing is written and the "
            "change is returned. Given: written only if the files and the change are still as "
            "they were then."
        ),
    )
    save_without_plan_check: bool = Field(
        default=False,
        description=(
            "In Production, the person's acknowledgement that plan checks could not run. "
            "Recorded in the audit with the actor."
        ),
    )


class SaveEntity(ProbeOwlModel):
    """A new entity to save on its own. Without base: preview it. With it: write it."""

    entity: str = Field(min_length=1, description="The entity's YAML.")
    environment: Name = Field(description="Where plans are read for the index checks.")
    base: str | None = Field(
        default=None,
        description=(
            "The base of the preview being confirmed. Omitted: nothing is written and the "
            "change is returned. Given: written only if the file and the change are still as "
            "they were then."
        ),
    )
    save_without_plan_check: bool = Field(
        default=False,
        description=(
            "In Production, the person's acknowledgement that plan checks could not run. "
            "Recorded in the audit with the actor."
        ),
    )


class EntityChange(CatalogChange):
    """A new entity's YAML file, and the save checks it passed."""

    report: SaveCheckReport


class ProbeChange(CatalogChange):
    """A probe's change to its YAML file, and the save checks it passed.

    With an entity, `base` covers both files and both changes, and is what to confirm with.
    """

    report: SaveCheckReport
    entity_change: CatalogChange | None = Field(
        default=None, description="The entity's change, when an entity was sent."
    )


class DraftProbe(ProbeOwlModel):
    """A probe as it is being edited, and optionally its entity, to check before saving."""

    probe: str = Field(min_length=1, description="The probe's YAML, as in the editor.")
    entity: str | None = Field(
        default=None,
        description="The entity's YAML when it is edited too; omitted: the loaded entity.",
    )
    environment: Name | None = Field(
        default=None, description="Where plans are read for the index checks."
    )


class DraftRun(ProbeOwlModel):
    """Run a draft probe without saving it, like any run: checked, guarded, stored, audited."""

    probe: str = Field(min_length=1, description="The probe's YAML, as in the editor.")
    entity: str | None = Field(default=None, description="The entity's YAML when it is edited too.")
    environment: Name
    value: str = Field(min_length=1, description="Any identifier of the subject.")
    identifier: FieldName | None = None
    investigation: InvestigationRecord


class RunsStarted(ProbeOwlModel):
    """Runs that have started, one per picked subject, in the order picked."""

    runs: list[RunStarted]


# Runbook steps as the API returns them: `runnable` is set by the server, never by the
# YAML author, so these live here and not on the YAML models.


class ReadQueryStepView(ReadQueryStep):
    """A read query step; runnable once reviewed and passing save checks."""

    runnable: bool = Field(description="Set by the server.")
    why_copy_only: str | None = Field(
        default=None, description="Why it is a reference query: not reviewed, or fails a check."
    )


class WriteQueryStepView(WriteQueryStep):
    """A write query step. Never runnable."""

    runnable: Literal[False] = Field(description="Write queries are never run by ProbeOwl.")
    relabelled: bool = Field(
        default=False, description="Its author called it a read query; the server overruled that."
    )


class ExternalQueryStepView(ExternalQueryStep):
    """An external query step. Copied, never run by ProbeOwl."""

    runnable: Literal[False]


class ManualStepView(ManualStep):
    """A manual step. Nothing to run."""

    runnable: Literal[False]


class ProbeStepView(ProbeStep):
    """A probe step; runnable when the probe exists in this application."""

    runnable: bool = Field(description="Set by the server.")


RunbookStepView = Annotated[
    ReadQueryStepView | WriteQueryStepView | ExternalQueryStepView | ManualStepView | ProbeStepView,
    Field(discriminator="kind"),
]


class RunbookView(RunbookBase):
    """A runbook as the API returns it, each step marked runnable or not."""

    steps: list[RunbookStepView]
    review_due: bool = Field(
        default=False,
        description="A reviewed step's review is over 90 days old. With no reviewed step: its "
        "last-verified date is.",
    )
    tables: list[str] = Field(default_factory=list, description="connector.table it uses.")
    drifted_tables: list[str] = Field(
        default_factory=list, description="The tables it uses that have drifted from the catalog."
    )


class SuggestedRunbook(ProbeOwlModel):
    """A runbook whose applies_to matches a run's outcomes."""

    runbook: RunbookView
    matched: list[str] = Field(description="The <location>.<outcome> it matched in this run.")
