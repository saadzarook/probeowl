# Author: @SaadZarook
"""The engine on the topup_completed example, against fake connectors with the planted data."""

import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from decimal import Decimal
from typing import Any

import pytest

from probeowl.engine.expressions import Unavailable, evaluate, parse
from probeowl.engine.locations import Connector, LocationRead
from probeowl.engine.runner import ProbeResult, _Data, run_probe
from probeowl.errors import ConfigError, ConnectorError, GuardViolation
from probeowl.model import Entity, Outcome, Probe, RunStatus
from probeowl.model.run import ExpectationResult
from tests.wallet_fakes import (
    ENTITY,
    PROBE,
    TOPUPS,
    Fake,
    connectors,
    ledger,
    payment,
    record,
    webhook,
)


def run(
    subject: str, entity: Entity = ENTITY, probe: Probe = PROBE, **changes: Connector | None
) -> ProbeResult:
    return run_probe(probe, entity, subject, connectors(**changes))


def outcomes(result: ProbeResult) -> dict[str, Outcome]:
    return {r.location: r.outcome for r in result.locations}


def expectation(result: ProbeResult, text: str) -> tuple[ExpectationResult, str | None]:
    [found] = [e for e in result.expectations if e.expectation.startswith(text)]
    return found.result, found.detail


# --- The planted cases ----------------------------------------------------------------------


def test_tp_91f2c7_fails_with_the_ledger_missing() -> None:
    result = run("tp_91f2c7")
    assert result.status is RunStatus.FAILED
    assert outcomes(result) == {
        "record": Outcome.FOUND,
        "payment": Outcome.FOUND,
        "webhook": Outcome.FOUND,
        "ledger": Outcome.MISSING,
    }
    assert expectation(result, "count(ledger) == 2") == (ExpectationResult.FAIL, "0 == 2")
    assert expectation(result, "count(webhook) >= 1") == (ExpectationResult.PASS, None)
    # The REST amount '500.00' equals the SQL Decimal 500.00.
    assert expectation(result, "payment.amount == record.amount")[0] is ExpectationResult.PASS
    assert expectation(result, "record.ledger_txn_id == null") == (
        ExpectationResult.SKIP,
        "when record.status == 'failed' is false",
    )


def test_tp_91f2b9_passes() -> None:
    result = run("tp_91f2b9")
    assert result.status is RunStatus.PASSED
    assert set(outcomes(result).values()) == {Outcome.FOUND}
    passed = [e for e in result.expectations if e.result is ExpectationResult.PASS]
    assert len(passed) == 6  # the failed-status step is skipped by its condition


def test_tp_93b001_shows_the_ledger_blocked_and_the_missing_link() -> None:
    result = run("tp_93b001")
    assert result.status is RunStatus.FAILED
    [ledger_result] = [r for r in result.locations if r.location == "ledger"]
    assert ledger_result.outcome is Outcome.BLOCKED
    assert ledger_result.reason == "missing link: record.ledger_txn_id is empty"
    assert ledger_result.evidence is None  # nothing was looked up
    result_, detail = expectation(result, "count(ledger) == 2")
    assert result_ is ExpectationResult.SKIP
    assert detail == "needs ledger, which is Blocked (missing link: record.ledger_txn_id is empty)"


def test_a_connector_error_is_that_locations_error_never_a_crashed_run() -> None:
    def down(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        raise ConnectorError("the API could not be reached: ConnectTimeout")

    result = run("tp_91f2b9", checkout_api=Fake(down))
    assert result.status is RunStatus.FAILED
    assert outcomes(result)["payment"] is Outcome.ERROR
    assert outcomes(result)["ledger"] is Outcome.FOUND
    [payment_result] = [r for r in result.locations if r.location == "payment"]
    assert payment_result.reason == "the API could not be reached: ConnectTimeout"
    assert expectation(result, "payment.amount == record.amount") == (
        ExpectationResult.SKIP,
        "needs payment, which is Error (the API could not be reached: ConnectTimeout)",
    )


def test_a_refused_query_is_an_error_too() -> None:
    def refuse(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        raise GuardViolation("only a SELECT may run")

    assert outcomes(run("tp_91f2b9", ledger_db=Fake(refuse)))["ledger"] is Outcome.ERROR


# --- Key graph, parallelism, fallback, cardinality --------------------------------------------


def test_locations_take_their_keys_from_the_record() -> None:
    fakes = connectors()
    run_probe(PROBE, ENTITY, "tp_91f2c7", fakes)
    ledger_db = fakes["ledger_db"]
    assert isinstance(ledger_db, Fake)
    assert ledger_db.calls[0][1] == {"topup_id": "tp_91f2c7", "ledger_txn_id": "lt_55e1a0"}


def test_locations_whose_keys_are_known_run_in_parallel() -> None:
    # payment, webhook and ledger all wait on record, then run together: on three
    # connectors at once, the barrier only opens if all three are in flight.
    barrier = threading.Barrier(3, timeout=5)

    def together(answer: Callable[[str, Mapping[str, object]], list[dict[str, Any]]]) -> Fake:
        def wait_then(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
            if "topups" not in lookup:
                barrier.wait()
            return answer(lookup, p)

        return Fake(wait_then)

    def wallet_db(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        return webhook(lookup, p) if "provider_webhooks" in lookup else record(lookup, p)

    result = run(
        "tp_91f2b9",
        wallet_db=together(wallet_db),
        checkout_api=together(payment),
        ledger_db=together(ledger),
    )
    assert result.status is RunStatus.PASSED


def test_an_empty_link_uses_the_fallback_and_reports_the_link() -> None:
    TOPUPS["tp_fallback"] = TOPUPS["tp_91f2b9"] | {"pay": None}
    try:
        result = run("tp_fallback")
    finally:
        del TOPUPS["tp_fallback"]
    [payment_result] = [r for r in result.locations if r.location == "payment"]
    assert payment_result.outcome is Outcome.FOUND
    assert payment_result.reason == (
        "missing link: record.provider_payment_id is empty; found by the fallback lookup"
    )


def test_more_than_one_record_for_a_one_location_is_ambiguous() -> None:
    def two_records(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        return record(lookup, p) * 2 if "topups" in lookup else webhook(lookup, p)

    result = run("tp_91f2b9", wallet_db=Fake(two_records))
    assert outcomes(result)["record"] is Outcome.AMBIGUOUS


def test_a_run_where_a_location_has_no_binding_is_refused_before_any_lookup() -> None:
    fakes = connectors(checkout_api=None)
    with pytest.raises(
        ConfigError, match=r"cannot run in this environment: payment \(checkout_api\)"
    ):
        run_probe(PROBE, ENTITY, "tp_91f2b9", fakes)
    assert all(isinstance(f, Fake) and not f.calls for f in fakes.values() if f is not None)


def test_each_location_is_reported_as_it_is_read() -> None:
    seen: list[str] = []
    run_probe(
        PROBE, ENTITY, "tp_91f2b9", connectors(), on_location=lambda r: seen.append(r.location)
    )
    assert seen[0] == "record"
    assert sorted(seen) == ["ledger", "payment", "record", "webhook"]


def test_a_probe_that_reads_an_unknown_location_fails_before_any_lookup() -> None:
    probe = PROBE.model_copy(
        update={"steps": [PROBE.steps[0].model_copy(update={"expect": ["exists(balance)"]})]}
    )
    fakes = connectors()
    with pytest.raises(ConfigError, match="reads balance, not a location of topup"):
        run_probe(probe, ENTITY, "tp_91f2b9", fakes)
    assert all(isinstance(f, Fake) and not f.calls for f in fakes.values())


def test_an_unknown_location_lists_the_locations_and_the_closest_one() -> None:
    def failing(expectation: str) -> str:
        probe = PROBE.model_copy(
            update={"steps": [PROBE.steps[0].model_copy(update={"expect": [expectation]})]}
        )
        with pytest.raises(ConfigError) as raised:
            run_probe(probe, ENTITY, "tp_91f2b9", connectors())
        return str(raised.value)

    assert failing("count(ledgers) >= 2").endswith(
        "reads ledgers, not a location of topup (did you mean ledger?); "
        "its locations are record, payment, webhook, ledger"
    )
    # Nothing close: only the list.
    assert failing("exists(balance)").endswith(
        "reads balance, not a location of topup; its locations are record, payment, webhook, ledger"
    )


# --- Masking ------------------------------------------------------------------------------


def test_pii_columns_and_rest_fields_are_masked_everywhere_they_leave_the_runner() -> None:
    entity = ENTITY.model_copy(deep=True)
    entity.locations["payment"].mask = ["customer.email"]
    probe = Probe.model_validate(
        {
            "probe": "masked",
            "kind": "validate",
            "entity": "topup",
            "owner": "t",
            "expect": ["record.customer_email == 'someone.else@example.com'", "exists(payment)"],
        }
    )
    result = run("tp_91f2b9", entity=entity, probe=probe)
    stored = json.dumps(asdict(result), default=str)
    assert "c.customer@example.com" not in stored
    [record_result] = [r for r in result.locations if r.location == "record"]
    [payment_result] = [r for r in result.locations if r.location == "payment"]
    assert record_result.evidence is not None and payment_result.evidence is not None
    assert record_result.evidence.rows[0]["customer_email"] == "[masked]"
    assert payment_result.evidence.rows[0]["customer"] == {"email": "[masked]"}
    # The expectation is still tested on the real value; only its detail is hidden.
    assert expectation(result, "record.customer_email") == (
        ExpectationResult.FAIL,
        "[masked] == 'someone.else@example.com'",
    )


def test_evidence_is_json_with_exact_numbers() -> None:
    result = run("tp_91f2b9")
    [record_result] = [r for r in result.locations if r.location == "record"]
    assert record_result.evidence is not None
    assert record_result.evidence.rows[0]["amount"] == "200.00"
    assert record_result.evidence.connector == "wallet_db"
    assert record_result.evidence.guards == ["fake guard"]


# --- A failed top-up: Blocked never means "does not exist" ---------------------------------


def test_a_failed_top_up_with_an_empty_link_passes() -> None:
    TOPUPS["tp_failed1"] = {
        "amount": Decimal("50.00"),
        "pay": "pay_f41l3d",
        "txn": None,
        "webhooks": 1,
        "ledger": 0,
        "status": "failed",
        "payment_status": "declined",
    }
    try:
        result = run("tp_failed1")
    finally:
        del TOPUPS["tp_failed1"]
    # The ledger cannot be looked up without a ledger_txn_id: Blocked, not Missing.
    assert outcomes(result)["ledger"] is Outcome.BLOCKED
    assert result.status is RunStatus.PASSED
    assert expectation(result, "record.ledger_txn_id == null") == (ExpectationResult.PASS, None)
    assert expectation(result, "payment.status != 'captured'") == (ExpectationResult.PASS, None)
    # The succeeded-status step is skipped by its condition, not by the Blocked ledger.
    assert expectation(result, "count(ledger) == 2") == (
        ExpectationResult.SKIP,
        "when record.status == 'succeeded' is false",
    )


def test_a_succeeded_top_up_with_an_empty_link_still_fails() -> None:
    result = run("tp_93b001")
    assert outcomes(result)["ledger"] is Outcome.BLOCKED
    assert result.status is RunStatus.FAILED


def test_locations_not_read_by_the_deadline_are_errors() -> None:
    release = threading.Event()

    def slow_ledger(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        release.wait(5)
        return ledger(lookup, p)

    started = time.monotonic()
    try:
        result = run_probe(
            PROBE,
            ENTITY,
            "tp_91f2b9",
            connectors(ledger_db=Fake(slow_ledger)),
            deadline=time.monotonic() + 0.3,
        )
    finally:
        release.set()
    assert time.monotonic() - started < 2  # the run does not wait for the slow lookup
    assert outcomes(result)["ledger"] is Outcome.ERROR
    [ledger_result] = [r for r in result.locations if r.location == "ledger"]
    assert ledger_result.reason == "run time limit reached"
    assert outcomes(result)["record"] is Outcome.FOUND
    assert result.status is RunStatus.FAILED


# --- Missing locations ----------------------------------------------------------------------

LEDGER_SKIP = (
    "needs ledger, which is Missing: if it may be absent, start the step with when: exists(ledger)"
)


def steps_probe(*steps: dict[str, Any]) -> Probe:
    return Probe.model_validate(
        {"probe": "p", "kind": "validate", "entity": "topup", "owner": "t", "steps": list(steps)}
    )


def test_a_balance_check_on_a_missing_ledger_is_skipped_not_passed_as_0_equals_0() -> None:
    result = run("tp_91f2c7")
    assert expectation(result, "sum(ledger.amount") == (ExpectationResult.SKIP, LEDGER_SKIP)
    assert expectation(result, "count(ledger) == 2") == (ExpectationResult.FAIL, "0 == 2")
    assert result.status is RunStatus.FAILED


def test_a_field_of_a_missing_location_is_skipped_and_fails_the_run() -> None:
    result = run("tp_91f2b9", checkout_api=Fake(lambda lookup, p: []))
    assert outcomes(result)["payment"] is Outcome.MISSING
    for text in ("payment.amount == record.amount", "payment.currency == record.currency"):
        assert expectation(result, text) == (
            ExpectationResult.SKIP,
            "needs payment, which is Missing: if it may be absent, start the step with "
            "when: exists(payment)",
        )
    assert expectation(result, "record.status in")[0] is ExpectationResult.PASS
    assert expectation(result, "count(ledger) == 2")[0] is ExpectationResult.PASS
    # A run cannot pass on data it could not see, as with Blocked.
    assert result.status is RunStatus.FAILED


def test_a_condition_that_reads_a_missing_location_skips_its_step() -> None:
    probe = steps_probe({"when": "payment.status == 'captured'", "expect": ["count(webhook) >= 1"]})
    result = run("tp_91f2b9", probe=probe, checkout_api=Fake(lambda lookup, p: []))
    [found] = result.expectations
    assert found.result is ExpectationResult.SKIP
    assert found.detail is not None and found.detail.startswith(
        "its condition needs payment, which is Missing"
    )
    assert result.status is RunStatus.FAILED


def test_when_exists_makes_a_location_optional() -> None:
    probe = steps_probe(
        {"expect": ["record.status == 'succeeded'"]},
        {"when": "exists(payment)", "expect": ["payment.amount == record.amount"]},
    )
    result = run("tp_91f2b9", probe=probe, checkout_api=Fake(lambda lookup, p: []))
    assert expectation(result, "payment.amount") == (
        ExpectationResult.SKIP,
        "when exists(payment) is false",
    )
    assert result.status is RunStatus.PASSED


def test_a_waiting_location_reads_as_a_missing_one() -> None:
    data = _Data({"webhook": LocationRead("webhook", Outcome.WAITING)})
    assert evaluate(parse("count(webhook) == 0"), data) is True
    with pytest.raises(Unavailable, match="needs webhook, which is Waiting"):
        evaluate(parse("all(webhook, status == 'delivered')"), data)
