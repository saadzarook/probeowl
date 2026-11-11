# Author: @SaadZarook
"""Fan-out (each), max_fanout, "showing N of M" and set expectations, on the card access example."""

from dataclasses import replace
from typing import Any

import pytest

from probeowl.connectors.lookup import LookupResult
from probeowl.engine.runner import ProbeResult, check_probe, run_probe
from probeowl.errors import ConfigError, ConnectorError
from probeowl.model import Entity, Outcome, Probe, RunStatus
from probeowl.model.run import ExpectationResult, LocationResult
from tests.card_fakes import CARDS, VISITOR, VISITOR_CARDS, card_connectors
from tests.wallet_fakes import Fake


def run(entity: Entity = VISITOR, probe: Probe = VISITOR_CARDS, **changes: Fake) -> ProbeResult:
    return run_probe(probe, entity, "VP00012931", card_connectors(**changes))


def expectation(result: ProbeResult, text: str) -> tuple[ExpectationResult, str | None]:
    [found] = [e for e in result.expectations if e.expectation.startswith(text)]
    return found.result, found.detail


def controller_lookups(result: ProbeResult) -> list[LocationResult]:
    return [r for r in result.locations if r.location == "controller"]


def with_max_fanout(n: int) -> Entity:
    data = VISITOR.model_dump(by_alias=True, exclude_none=True)
    data["locations"]["cards"]["each"]["controller"]["max_fanout"] = n
    return Entity.model_validate(data)


def probe(*expect: str) -> Probe:
    return Probe.model_validate(
        {
            "probe": "visitor_cards",
            "kind": "inspect",
            "entity": "visitor",
            "owner": "access-team",
            "expect": list(expect),
        }
    )


# --- The planted cases ----------------------------------------------------------------------


def test_vp00012931_shows_the_three_planted_card_issues() -> None:
    result = run()
    assert result.status is RunStatus.FAILED
    assert expectation(result, "count(cards where status == 'active') <= 1") == (
        ExpectationResult.FAIL,
        "2 <= 1",
    )
    assert expectation(result, "all(cards where status == 'active', valid_to > now())") == (
        ExpectationResult.FAIL,
        "fails for C198220",
    )
    assert expectation(result, "all(cards, controller.status == status)") == (
        ExpectationResult.FAIL,
        "fails for C177031",
    )


def test_the_controller_is_looked_up_once_per_card_each_with_its_outcome() -> None:
    connectors = card_connectors()
    result = run_probe(VISITOR_CARDS, VISITOR, "VP00012931", connectors)
    lookups = controller_lookups(result)
    assert [(r.parent, r.row, r.outcome) for r in lookups] == [
        ("cards", 0, Outcome.FOUND),
        ("cards", 1, Outcome.FOUND),
        ("cards", 2, Outcome.FOUND),
    ]
    assert [p for _, p in connectors["door_controller_api"].calls] == [
        {**CARDS[i], "visitor_profile_id": "VP00012931"} for i in range(3)
    ]
    assert lookups[2].evidence is not None
    assert lookups[2].evidence.parameters["card_id"] == "C177031"
    # Fan-out results follow their parent, in row order.
    names = [r.location for r in result.locations]
    assert names.index("controller") == names.index("cards") + 1


def test_each_fan_out_result_is_reported_as_it_arrives() -> None:
    seen: list[tuple[str, int | None]] = []
    run_probe(
        VISITOR_CARDS,
        VISITOR,
        "VP00012931",
        card_connectors(),
        on_location=lambda r: seen.append((r.location, r.row)),
    )
    assert ("controller", 0) in seen and ("controller", 2) in seen
    assert seen.index(("cards", None)) < seen.index(("controller", 0))


# --- max_fanout ------------------------------------------------------------------------------


def test_max_fanout_caps_the_lookups_and_says_how_many_ran() -> None:
    connectors = card_connectors()
    result = run_probe(VISITOR_CARDS, with_max_fanout(2), "VP00012931", connectors)
    assert len(connectors["door_controller_api"].calls) == 2
    assert [r.row for r in controller_lookups(result)] == [0, 1]
    [cards] = [r for r in result.locations if r.location == "cards"]
    assert cards.reason == "controller looked up for 2 of 3 rows (max_fanout 2)"
    # The third card was never checked, so the expectation cannot pass: it is skipped.
    assert expectation(result, "all(cards, controller.status == status)") == (
        ExpectationResult.SKIP,
        "skipped: 1 card not checked (max_fanout 2)",
    )
    assert result.status is RunStatus.FAILED


def test_rows_past_max_fanout_outside_the_filter_do_not_matter() -> None:
    result = run_probe(
        probe("all(cards where status == 'active', controller.status == status)"),
        with_max_fanout(2),
        "VP00012931",
        card_connectors(),
    )
    # Rows 1 and 2 are the active ones, and both were looked up.
    assert expectation(result, "all(cards") == (ExpectationResult.PASS, None)
    assert result.status is RunStatus.PASSED


# --- Outcomes per row ------------------------------------------------------------------------


def test_a_failed_lookup_for_one_row_is_an_error_for_that_row_and_fails_the_run() -> None:
    def flaky(lookup: str, p: Any) -> list[dict[str, Any]]:
        if p["card_id"] == "C198220":
            raise ConnectorError("the request timed out after 2 s")
        return [{"card_id": p["card_id"], "status": "active"}]

    result = run(door_controller_api=Fake(flaky))
    outcomes = [(r.row, r.outcome, r.reason) for r in controller_lookups(result)]
    assert outcomes[1] == (1, Outcome.ERROR, "the request timed out after 2 s")
    assert outcomes[0][1] is Outcome.FOUND and outcomes[2][1] is Outcome.FOUND
    assert expectation(result, "all(cards, controller")[0] is ExpectationResult.SKIP
    assert result.status is RunStatus.FAILED


def test_a_card_the_controller_does_not_know_is_missing_for_that_row() -> None:
    result = run(door_controller_api=Fake(lambda lookup, p: []))
    assert {r.outcome for r in controller_lookups(result)} == {Outcome.MISSING}
    # Its status is unknown, not null: the check is skipped, never failed or passed on null.
    assert expectation(result, "all(cards, controller") == (
        ExpectationResult.SKIP,
        "needs controller, which is Missing for 3 cards",
    )
    assert result.status is RunStatus.FAILED


def test_the_skip_counts_the_cards_whose_controller_record_is_missing() -> None:
    def all_but_one(lookup: str, p: Any) -> list[dict[str, Any]]:
        return [] if p["card_id"] == "C177031" else [{"card_id": p["card_id"], "status": "x"}]

    result = run(door_controller_api=Fake(all_but_one))
    assert expectation(result, "all(cards, controller") == (
        ExpectationResult.SKIP,
        "needs controller, which is Missing for 1 card",
    )


def test_no_cards_means_no_lookups_and_only_counting_is_checked() -> None:
    connectors = card_connectors(access_db=Fake(lambda lookup, p: []))
    result = run_probe(
        probe(
            "all(cards, controller.status == status)",
            "count(cards where status == 'active') <= 1",
            "none(cards, status == 'revoked')",
        ),
        VISITOR,
        "VP1",
        connectors,
    )
    assert not connectors["door_controller_api"].calls
    assert controller_lookups(result) == []
    assert expectation(result, "all(cards") == (
        ExpectationResult.SKIP,
        "needs cards, which is Missing: if it may be absent, start the step with "
        "when: exists(cards)",
    )
    assert expectation(result, "count(cards")[0] is ExpectationResult.PASS
    assert expectation(result, "none(cards")[0] is ExpectationResult.PASS
    assert result.status is RunStatus.FAILED


# --- unique and the details ------------------------------------------------------------------


def test_unique_names_the_repeated_values() -> None:
    twice = [dict(c) for c in CARDS] + [dict(CARDS[0])]
    result = run_probe(
        probe("unique(cards.card_id)", "unique(cards.access_group where status == 'revoked')"),
        VISITOR,
        "VP00012931",
        card_connectors(access_db=Fake(lambda lookup, p: twice)),
    )
    assert expectation(result, "unique(cards.card_id)") == (
        ExpectationResult.FAIL,
        "repeated: C204518 (2 times)",
    )
    assert expectation(result, "unique(cards.access_group")[0] is ExpectationResult.PASS


def test_none_names_the_rows_it_found() -> None:
    result = run(probe=probe("none(cards, status == 'revoked')"))
    assert expectation(result, "none(") == (ExpectationResult.FAIL, "found C177031")


def test_more_failing_rows_than_shown_are_counted() -> None:
    many = [{**CARDS[1], "card_id": f"C{i:06d}"} for i in range(8)]
    result = run_probe(
        probe("all(cards, valid_to > now())"),
        VISITOR,
        "VP00012931",
        card_connectors(access_db=Fake(lambda lookup, p: many)),
    )
    assert expectation(result, "all(")[1] == (
        "fails for C000000, C000001, C000002, C000003, C000004 and 3 more"
    )


# --- Checked when a probe loads --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("controller.status == 'active'", "read it inside all(cards, ...)"),
        ("count(controller) == 3", "counts controller, which is looked up per row of cards"),
        ("all(profile, controller.status == 'active')", "read it inside all(cards, ...)"),
    ],
)
def test_a_fan_out_location_is_read_per_row_of_its_parent(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message.replace("(", r"\(").replace(")", r"\)")):
        check_probe(probe(text), VISITOR)


# --- More rows than a location keeps --------------------------------------------------------


class Counted(Fake):
    """A SQL connector that counts the rows past its cap, as the SQL adapters do."""

    def lookup(self, lookup: str, parameters: Any, max_rows: int, /) -> LookupResult:
        result = super().lookup(lookup, parameters, max_rows)
        total = len(self.answer(lookup, parameters)) if result.truncated else None
        return replace(result, rows_total=total)


def test_a_many_location_past_max_rows_says_showing_n_of_m() -> None:
    many = [{**CARDS[0], "card_id": f"C{i:06d}"} for i in range(312)]
    result = run(probe=probe("count(cards) >= 1"), access_db=Counted(lambda lookup, p: many))
    [cards] = [r for r in result.locations if r.location == "cards"]
    assert cards.outcome is Outcome.FOUND
    # max_rows 50 in the example; the controller then fans out over 10 of those 50.
    assert (
        cards.reason == "showing 50 of 312; controller looked up for 10 of 50 rows (max_fanout 10)"
    )
    assert cards.evidence is not None and cards.evidence.rows_total == 312
    assert len(cards.evidence.rows) == 50


def test_the_skip_names_how_many_rows_were_not_checked_and_the_limit() -> None:
    twelve = [{**CARDS[0], "card_id": f"C{i:06d}"} for i in range(12)]
    knows_every_card = Fake(lambda lookup, p: [{"card_id": p["card_id"], "status": "active"}])
    result = run(access_db=Fake(lambda lookup, p: twelve), door_controller_api=knows_every_card)
    assert expectation(result, "all(cards, controller.status == status)") == (
        ExpectationResult.SKIP,
        "skipped: 2 cards not checked (max_fanout 10)",
    )
    assert result.status is RunStatus.FAILED
