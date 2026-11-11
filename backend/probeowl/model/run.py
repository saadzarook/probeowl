# Author: @SaadZarook
"""Run, outcome, evidence and investigation record models."""

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, JsonValue, model_validator

from probeowl.model.common import FieldName, FieldRef, Name, ProbeOwlModel
from probeowl.model.probe import ProbeKind


class Outcome(StrEnum):
    """What one location returned."""

    FOUND = "found"
    MISSING = "missing"
    WAITING = "waiting"
    BLOCKED = "blocked"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"
    STALE = "stale"
    NOT_CONFIGURED = "not_configured"


class RunStatus(StrEnum):
    """Where a run is.

    Skipped: a guard or circuit breaker stopped it before any lookup. Ambiguous: the
    identifier matched several subjects, so nothing else ran; see the run's matches.
    """

    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    AMBIGUOUS = "ambiguous"


class ExpectationResult(StrEnum):
    """How one expectation ended. Skip: an input it needs is Blocked."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


class ActorKind(StrEnum):
    """Who started a run."""

    PERSON = "person"
    SCHEDULER = "scheduler"
    AGENT = "agent"


class Reason(StrEnum):
    """Why an investigation was run."""

    INCIDENT = "incident"
    CUSTOMER_COMPLAINT = "customer_complaint"
    DEPLOY_VERIFICATION = "deploy_verification"
    AUDIT_OR_COMPLIANCE = "audit_or_compliance"
    PROACTIVE_CHECK = "proactive_check"


class Conclusion(StrEnum):
    """What an investigation concluded, added after the run."""

    ROOT_CAUSE_FOUND = "root_cause_found"
    NO_ISSUE = "no_issue"
    ESCALATED = "escalated"


class ValueKind(StrEnum):
    """What type a value was when it was read, since evidence keeps dates and decimals as text."""

    DATETIME = "datetime"
    DATE = "date"
    TIME = "time"
    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    BOOLEAN = "boolean"
    TEXT = "text"


class Evidence(ProbeOwlModel):
    """The exact query, parameters, guards and raw result behind an outcome. Values are masked."""

    connector: Name
    query: str = Field(description="The statement or request as sent, after guards rewrote it.")
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    guards: list[str] = Field(
        default_factory=list, description="Guards applied, such as SET LOCK_TIMEOUT 0."
    )
    rows: list[dict[str, JsonValue]] = Field(default_factory=list)
    rows_total: int | None = Field(
        default=None, ge=0, description="Rows that matched, when more than were returned."
    )
    column_types: dict[str, ValueKind] = Field(
        default_factory=dict,
        description="Each column's type as read, so a value can be bound as that type again.",
    )
    started_at: datetime
    duration_ms: int = Field(ge=0)


class LocationResult(ProbeOwlModel):
    """The outcome of one location, or of one fan-out lookup for one row."""

    always_returned = ("title",)

    location: Name
    title: str = Field(
        default="", description="The location's title, set when the run is read: never stored."
    )
    parent: Name | None = Field(default=None, description="The many location this fans out from.")
    row: int | None = Field(default=None, ge=0, description="The parent row this lookup is for.")
    outcome: Outcome
    reason: str | None = Field(
        default=None, description="Why, when not Found: the error, or the missing link."
    )
    caveat: str | None = Field(
        default=None,
        description=(
            "Why this outcome may not be final. Set on a Missing record read from a Production "
            "replica whose lag is unknown: the record may not have arrived yet."
        ),
    )
    evidence: Evidence | None = Field(
        default=None, description="Absent when no lookup ran (Blocked, Not configured)."
    )

    @model_validator(mode="after")
    def _parent_and_row_together(self) -> Self:
        if (self.parent is None) != (self.row is None):
            raise ValueError("parent and row are set together, for fan-out lookups only")
        return self


class HealthyAttemptStatus(StrEnum):
    """Where an attempt to compare a run with a known-good subject is."""

    RUNNING = "running"
    COMPARED = "compared"
    NO_CANDIDATES = "no_candidates"
    NONE_PASSED = "none_passed"
    COULD_NOT_RUN = "could_not_run"


class HealthyAttempt(ProbeOwlModel):
    """The latest attempt to find a known-good subject for this run."""

    status: HealthyAttemptStatus
    narrow: Name | None = Field(default=None, description="The closer match chosen, if any.")
    pasted: bool = Field(default=False, description="A person gave the known-good ID.")
    replaced: bool = Field(default=False, description="It replaced an earlier attempt on this run.")
    replaced_subject: str | None = Field(
        default=None, description="Who the attempt it replaced compared with, if it got that far."
    )
    runs: list[str] = Field(default_factory=list, description="The candidates' runs, in order.")
    healthy_run: str | None = Field(default=None, description="The run compared with.")
    message: str | None = Field(default=None, description="Why there is nothing to compare.")
    sample: Evidence | None = Field(
        default=None, description="The sample lookup behind the candidates: root keys only."
    )


class Match(ProbeOwlModel):
    """One subject an identifier found, with the record it was found in. Values are masked."""

    subject: str = Field(description="The subject's root key value.")
    fields: dict[str, JsonValue] = Field(
        default_factory=dict, description="The via location's record for this subject."
    )


class ExpectationEvaluation(ProbeOwlModel):
    """The result of one expectation in one run."""

    step: int = Field(ge=0, description="Index of the step in the probe.")
    expectation: str
    result: ExpectationResult
    detail: str | None = Field(default=None, description="Values seen, or why it was skipped.")
    caveat: str | None = Field(
        default=None,
        description=(
            "Why this result may not be final: it read a Missing record that carries a caveat."
        ),
    )


class Actor(ProbeOwlModel):
    """Who started a run."""

    kind: ActorKind
    name: str = Field(min_length=1, description="The operating system user, or 'scheduler'.")
    on_behalf_of: str | None = Field(default=None, description="The person an agent acts for.")

    @model_validator(mode="after")
    def _agent_acts_for_a_person(self) -> Self:
        if (self.kind is ActorKind.AGENT) != (self.on_behalf_of is not None):
            raise ValueError("on_behalf_of is required for an agent and only for an agent")
        return self


class InvestigationRecord(ProbeOwlModel):
    """Why a person ran an investigation. Reference is mandatory in Production."""

    reason: Reason
    reference: str | None = Field(
        default=None,
        pattern=r"^\S+$",
        max_length=64,
        description="Jira key, support case ID or change ID, such as WALLET-482.",
    )
    notes: str | None = None
    customer_reference: str | None = Field(default=None, description="Stored masked.")
    related_runs: list[str] = Field(default_factory=list)
    conclusion: Conclusion | None = None


class Run(ProbeOwlModel):
    """One execution of a probe, with every lookup, result and timing. Read while running too."""

    always_returned = ("probe_title",)

    id: str
    application: Name
    environment: Name
    probe: Name
    probe_title: str = Field(
        default="", description="The probe's title, set when the run is read: never stored."
    )
    kind: ProbeKind
    probe_version: str
    entity: Name
    identifier: FieldName = Field(description="The identifier the run was started from.")
    identifier_value: str = Field(description="As entered; masked when the identifier is PII.")
    subject: str | None = Field(
        default=None, description="The root key value, once resolved. None when Ambiguous."
    )
    via: FieldRef | None = Field(
        default=None,
        description="<location>.<field> the identifier was looked up in. None for the root key.",
    )
    resolution: LocationResult | None = Field(
        default=None,
        description=(
            "The lookup that found the subject from the identifier, with its evidence: "
            "Found, Missing, Ambiguous or Error. None for the root key."
        ),
    )
    matches: list[Match] = Field(
        default_factory=list,
        description="The subjects the identifier found. Several make the run Ambiguous.",
    )
    from_run: str | None = Field(
        default=None, description="The Ambiguous run this run's subject was picked from."
    )
    healthy_for: str | None = Field(
        default=None, description="The run this one is a known-good candidate to compare with."
    )
    healthy_attempt: HealthyAttempt | None = Field(
        default=None, description="The latest attempt to compare this run with a healthy one."
    )
    status: RunStatus
    skip_reason: str | None = None
    stop_reason: str | None = Field(
        default=None,
        description=(
            "Why a failed run stopped before it finished: 'run time limit reached', or "
            "'interrupted' when owl serve stopped while it ran."
        ),
    )
    actor: Actor
    investigation: InvestigationRecord | None = None
    started_at: datetime
    finished_at: datetime | None = None
    locations: list[LocationResult] = Field(
        default_factory=list, description="Filled in as each location's outcome arrives."
    )
    expectations: list[ExpectationEvaluation] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "What limits how far this run's results can be trusted, without failing it: "
            "a Production replica whose lag is unknown."
        ),
    )
    caveat: str | None = Field(
        default=None,
        description=(
            "Why the status may not be final, counting the Missing records that carry a "
            "caveat. The status itself is unchanged."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.status is RunStatus.AMBIGUOUS and (
            self.subject is not None or len(self.matches) < 2
        ):
            raise ValueError("an Ambiguous run has several matches and no subject")
        if (self.status is RunStatus.SKIPPED) != (self.skip_reason is not None):
            raise ValueError("skip_reason is required for a skipped run and only for one")
        if (self.status is RunStatus.RUNNING) != (self.finished_at is None):
            raise ValueError("finished_at is set once a run is no longer running, and only then")
        if self.actor.kind is not ActorKind.SCHEDULER and self.investigation is None:
            raise ValueError("a run started by a person needs an investigation record")
        return self


DRAFT_VERSION_PREFIX = "draft:"


class RunSummary(ProbeOwlModel):
    """One run as the Runs list shows it: no locations, expectations or evidence."""

    always_returned = ("probe_title",)

    id: str
    application: Name
    environment: Name
    probe: Name
    probe_title: str = Field(
        default="", description="The probe's title, set when the list is read: never stored."
    )
    kind: ProbeKind
    draft: bool = Field(description="A test run of a draft from the Probe editor.")
    identifier: FieldName
    identifier_value: str = Field(description="As entered; masked when the identifier is PII.")
    subject: str | None = Field(default=None, description="The root key value, once resolved.")
    status: RunStatus
    stop_reason: str | None = Field(
        default=None, description="Why it stopped early: a kill switch or the time limit."
    )
    skip_reason: str | None = None
    caveat: str | None = Field(default=None, description="Why the status may not be final.")
    actor: str = Field(description="Who started it.")
    reason: Reason | None = None
    reference: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
