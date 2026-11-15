# Author: @SaadZarook
"""The run status rule (#81): Failed, then Not checked, then Passed, with exit codes 1, 2, 0."""

from types import SimpleNamespace
from typing import cast

import pytest
import typer

from probeowl.cli import server
from probeowl.engine.expressions import Unavailable
from probeowl.engine.locations import LocationRead, unreadable
from probeowl.engine.runner import run_status
from probeowl.model.run import (
    ExpectationEvaluation,
    ExpectationResult,
    LocationResult,
    Outcome,
    Run,
    RunStatus,
)


def expectation(result: ExpectationResult) -> ExpectationEvaluation:
    return ExpectationEvaluation(step=0, expectation="count(ledger) == 2", result=result)


def location(outcome: Outcome) -> LocationResult:
    return LocationResult(location="ledger", outcome=outcome)


PASS, FAIL, SKIP = ExpectationResult.PASS, ExpectationResult.FAIL, ExpectationResult.SKIP
ABSENT = Unavailable("ledger", "Missing", absent=True)
UNREADABLE = Unavailable("ledger", "Error")


@pytest.mark.parametrize(
    ("results", "outcomes", "unusable", "status"),
    [
        # A failed expectation is Failed, whatever else could not be checked.
        ([PASS, FAIL], [Outcome.FOUND], [], RunStatus.FAILED),
        ([FAIL, SKIP], [Outcome.ERROR], [UNREADABLE], RunStatus.FAILED),
        # Data read and absent (Missing, an empty link) is a finding: Failed.
        ([PASS, SKIP], [Outcome.MISSING], [ABSENT], RunStatus.FAILED),
        ([SKIP], [Outcome.BLOCKED], [ABSENT, UNREADABLE], RunStatus.FAILED),
        # Nothing failed, but data could not be read: Not checked, even if others passed.
        ([PASS, PASS, SKIP], [Outcome.FOUND, Outcome.STALE], [UNREADABLE], RunStatus.NOT_CHECKED),
        ([SKIP, SKIP], [Outcome.ERROR, Outcome.ERROR], [UNREADABLE], RunStatus.NOT_CHECKED),
        # An Error location that no expectation reads still means something went unchecked.
        ([PASS], [Outcome.FOUND, Outcome.ERROR], [], RunStatus.NOT_CHECKED),
        # Everything evaluated held; a false condition counts against nothing.
        ([PASS, SKIP], [Outcome.FOUND], [], RunStatus.PASSED),
        ([], [Outcome.FOUND], [], RunStatus.PASSED),
    ],
)
def test_the_run_status_rule(
    results: list[ExpectationResult],
    outcomes: list[Outcome],
    unusable: list[Unavailable],
    status: RunStatus,
) -> None:
    found = run_status([expectation(r) for r in results], [location(o) for o in outcomes], unusable)
    assert found is status


@pytest.mark.parametrize(
    ("read", "cannot_read"),
    [
        (LocationRead("ledger", Outcome.ERROR), True),
        (LocationRead("ledger", Outcome.STALE), True),
        (LocationRead("ledger", Outcome.NOT_CONFIGURED), True),
        # Not arrived yet is not known to be absent.
        (LocationRead("ledger", Outcome.WAITING), True),
        (LocationRead("ledger", Outcome.MISSING), False),
        # Blocked: by where its chain starts.
        (
            LocationRead(
                "ledger", Outcome.BLOCKED, reason="record is Error", unreadable_chain=True
            ),
            True,
        ),
        (
            LocationRead(
                "ledger", Outcome.BLOCKED, reason="missing link: record.ledger_txn_id is empty"
            ),
            False,
        ),
        (LocationRead("ledger", Outcome.BLOCKED, reason="record has 2 records"), False),
    ],
)
def test_what_could_not_be_read_and_what_is_absent(read: LocationRead, cannot_read: bool) -> None:
    assert unreadable(read) is cannot_read


def run(status: RunStatus, *, caveat: str | None = None, stop: str | None = None) -> Run:
    """What finish() reads of a run: its id, status, warnings, reasons and caveat."""
    fields = SimpleNamespace(
        id="run_1", status=status, warnings=[], stop_reason=stop, skip_reason=None, caveat=caveat
    )
    return cast(Run, fields)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (RunStatus.FAILED, server.FAILED),
        (RunStatus.NOT_CHECKED, server.NOT_RUN),
        (RunStatus.PASSED, server.PASSED),
        (RunStatus.AMBIGUOUS, server.AMBIGUOUS),
    ],
)
def test_exit_codes_follow_the_rule(status: RunStatus, code: int) -> None:
    with pytest.raises(typer.Exit) as exited:
        server.finish(run(status))
    assert exited.value.exit_code == code
    assert (server.FAILED, server.NOT_RUN, server.PASSED) == (1, 2, 0)


def test_a_not_checked_run_says_why(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit):
        server.finish(run(RunStatus.NOT_CHECKED, stop="the identifier could not be looked up"))
    assert (
        "Run run_1: not_checked (the identifier could not be looked up)" in capsys.readouterr().out
    )


def test_a_failed_missing_on_a_replica_of_unknown_lag_keeps_its_caveat(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Failed, not certain: the record may not have arrived yet, and the output says so.
    caveat = "replica lag unknown, the record may not have arrived yet"
    with pytest.raises(typer.Exit) as exited:
        server.finish(run(RunStatus.FAILED, caveat=caveat))
    assert exited.value.exit_code == server.FAILED
    assert f"Run run_1: failed - {caveat}" in capsys.readouterr().out
