# Author: @SaadZarook
"""A REST location shows only the fields it exposes: every other field of its answer is masked.

An API answers with whatever it has, a customer's details too, and nobody knows its fields before
the first lookup. So a REST field is shown only when the entity says it may be, in every run, a
draft's too, in what was stored before, and in a healthy comparison, which compares it by
fingerprint.
"""

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from probeowl.checks.rules import unexposed_warnings
from probeowl.cli import main as cli
from probeowl.engine.expressions import fields_used, parse
from probeowl.engine.masking import MASKED, Masked, is_rest, mask_rows, masked_fields
from probeowl.model.check import load_applications
from probeowl.model.entity import Entity
from probeowl.store.runs import get_run, save_run
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import EXAMPLES, Setup
from tests.test_healthy_comparison import (
    HEALTHY,
    compared,
    failed_run,
    row,
    sample_finds,
    started,
)
from tests.test_pii_subject import everything_stored
from tests.test_runs import finished, start

TOPUP = "tp_91f2c7"
PAYMENT = "pay_7hq2m9"
EMAIL = "c.customer@example.com"
CARD_PROBE = {"application": "card_access", "probe": "visitor_cards", "value": "VP00012931"}


def payment_of(run: dict[str, Any]) -> dict[str, Any]:
    [found] = [r for r in run["locations"] if r["location"] == "payment"]
    return dict(found)


def change_payment(setup: Setup, **update: Any) -> None:
    entities = setup.applications["wallet"].entities
    entity = entities["topup"]
    locations = dict(entity.locations)
    locations["payment"] = locations["payment"].model_copy(update=update)
    entities["topup"] = entity.model_copy(update={"locations": locations})


def answers(setup: Setup, **more: Any) -> None:
    """The checkout API's answer for a payment carries these fields too."""
    fake = setup.fakes["checkout_api"]
    real = fake.answer

    def answer(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        return [{**r, **more} for r in real(lookup, p)]

    fake.answer = answer


# --- The rule -----------------------------------------------------------------------------------


def test_a_field_a_rest_location_does_not_expose_is_masked_whole_and_a_null_stays_null() -> None:
    masked = Masked(frozenset({"customer.email"}), frozenset({"status", "customer"}))
    rows = [
        {
            "status": "captured",
            "customer": {"email": EMAIL, "tier": "gold"},
            "id": PAYMENT,
            "card": {"last4": "4242"},
            "events": ["a", "b"],
            "refunded_at": None,
            "Status_Code": 200,
        }
    ]
    assert mask_rows(rows, masked) == [
        {
            "status": "captured",
            "customer": {"email": MASKED, "tier": "gold"},
            "id": MASKED,
            "card": MASKED,
            "events": MASKED,
            "refunded_at": None,
            "Status_Code": MASKED,
        }
    ]


def test_what_is_masked_is_asked_by_name_or_path_without_case() -> None:
    masked = Masked(frozenset({"customer"}), frozenset({"status", "customer"}))
    assert "STATUS" not in masked and "status" not in masked
    assert "customer" in masked and "customer.email" in masked  # inside a masked field
    assert "id" in masked and "card.last4" in masked  # not exposed
    sql = Masked(frozenset({"customer_email"}))
    assert "customer_email" in sql and "anything_else" not in sql


def test_only_a_get_is_a_rest_location_and_sql_masks_what_it_lists() -> None:
    entity = Entity.model_validate_json(_topup().model_dump_json())
    record, payment = entity.locations["record"], entity.locations["payment"]
    assert is_rest(payment) and not is_rest(record)
    assert masked_fields(entity, record).shown is None
    assert masked_fields(entity, payment).shown == frozenset({"status", "amount", "currency"})
    assert "app_version" not in masked_fields(entity, record)


def test_a_list_of_plain_values_is_masked_too() -> None:
    masked = Masked(frozenset(), frozenset({"status"}))
    assert mask_rows([{"value": "a@b.example"}], masked) == [{"value": MASKED}]


# --- In a run -----------------------------------------------------------------------------------


def test_a_run_shows_the_exposed_fields_and_masks_the_rest(setup: Setup) -> None:
    api = setup.client()
    run = finished(api, start(api, value=TOPUP))
    [answer] = payment_of(run)["evidence"]["rows"]
    assert answer == {
        "id": MASKED,
        "status": "captured",
        "amount": "500.00",
        "currency": "USD",
        "customer": MASKED,
    }
    assert EMAIL not in api.get(f"/api/runs/{run['id']}").text
    assert EMAIL not in everything_stored(setup)


def test_exposing_a_field_shows_it_from_the_next_lookup(setup: Setup) -> None:
    change_payment(setup, exposes=["status", "amount", "currency", "id"])
    api = setup.client()
    [answer] = payment_of(finished(api, start(api, value=TOPUP)))["evidence"]["rows"]
    assert answer["id"] == PAYMENT and answer["customer"] == MASKED


def test_an_expectation_reads_the_real_value_and_its_detail_hides_it(setup: Setup) -> None:
    probe = (EXAMPLES / "wallet" / "probes" / "topup_completed.yaml").read_text(encoding="utf-8")
    draft = probe.replace(
        "  - expect: count(webhook) >= 1",
        "\n".join(
            ["  - expect:", f"      - payment.id == '{PAYMENT}'", "      - payment.id == 'other'"]
        ),
    )
    assert draft != probe
    api = setup.client()
    response = api.post(
        "/api/applications/wallet/probes/test-runs",
        json={
            "probe": draft,
            "environment": "uat",
            "value": TOPUP,
            "investigation": {"reason": "proactive_check"},
        },
    )
    assert response.status_code == 202, response.text
    run = finished(api, response.json()["id"])
    results = {e["expectation"]: e for e in run["expectations"]}
    # Tested on the real value: masking is only what leaves the runner.
    assert results[f"payment.id == '{PAYMENT}'"]["result"] == "pass"
    failed = results["payment.id == 'other'"]
    assert failed["result"] == "fail" and failed["detail"] == f"{MASKED} == 'other'"


def test_a_draft_run_masks_what_its_draft_does_not_expose(setup: Setup) -> None:
    entity = (EXAMPLES / "wallet" / "entities" / "topup.yaml").read_text(encoding="utf-8")
    nothing_exposed = entity.replace("    exposes: [status, amount, currency]\n", "")
    assert nothing_exposed != entity
    probe = (
        "probe: topup_sample\nkind: inspect\nentity: topup\nowner: t\nexpect:\n  - exists(record)\n"
    )
    api = setup.client()
    response = api.post(
        "/api/applications/wallet/probes/test-runs",
        json={
            "probe": probe,
            "entity": nothing_exposed,
            "environment": "uat",
            "value": TOPUP,
            "investigation": {"reason": "proactive_check"},
        },
    )
    assert response.status_code == 202, response.text
    [answer] = payment_of(finished(api, response.json()["id"]))["evidence"]["rows"]
    # The names show, so a person can choose what to expose; no value does.
    assert answer == dict.fromkeys(["id", "status", "amount", "currency", "customer"], MASKED)


def test_a_fan_out_masks_what_it_does_not_expose_on_every_row(setup: Setup) -> None:
    api = setup.client()
    run = finished(api, start(api, **CARD_PROBE))
    doors = [r for r in run["locations"] if r["location"] == "controller" and r["evidence"]]
    rows = [answer for door in doors for answer in door["evidence"]["rows"]]
    assert rows and all(set(answer) == {"card_id", "status"} for answer in rows)
    assert all(answer["card_id"] == MASKED and answer["status"] != MASKED for answer in rows)


# --- Healthy comparison -------------------------------------------------------------------------


@pytest.mark.parametrize(("healthy_tier", "result"), [("gold", "same"), ("silver", "different")])
def test_a_compare_field_not_exposed_is_compared_by_fingerprint(
    setup: Setup, healthy_tier: str, result: str
) -> None:
    change_payment(setup, compare=["status", "amount", "currency", "tier"])
    fake = setup.fakes["checkout_api"]
    real = fake.answer

    def answer(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        healthy = p.get("provider_payment_id") == "pay_4kd8x1"  # the healthy top-up's
        return [{**r, "tier": healthy_tier if healthy else "gold"} for r in real(lookup, p)]

    fake.answer = answer
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    tier = row(started(api, run_id), "payment", "tier")
    assert tier["masked"] is True and tier["result"] == result
    assert tier["failing"] is None and tier["healthy"] is None
    assert row(compared_again(api, run_id), "payment", "status")["masked"] is False


def compared_again(api: Any, run_id: str) -> dict[str, Any]:
    return compared(api, run_id)


# --- Runs stored before this --------------------------------------------------------------------


def stored_before(setup: Setup) -> str:
    """A run as 0.2.4 stored it: every field of the payment's answer, and a detail naming one."""
    api = setup.client()
    run_id = str(finished(api, start(api, value=TOPUP))["id"])
    with setup.store.begin() as conn:
        run = get_run(conn, LOCAL_WORKSPACE, run_id)
        locations = []
        for each in run.locations:
            if each.location == "payment" and each.evidence is not None:
                rows = [
                    {**r, "id": PAYMENT, "customer": {"email": EMAIL}} for r in each.evidence.rows
                ]
                each = each.model_copy(
                    update={"evidence": each.evidence.model_copy(update={"rows": rows})}
                )
            locations.append(each)
        details = [
            e.model_copy(update={"detail": f"'{EMAIL}' == 'x'"}) if index == 0 else e
            for index, e in enumerate(run.expectations)
        ]
        save_run(
            conn,
            LOCAL_WORKSPACE,
            run.model_copy(update={"locations": locations, "expectations": details}),
        )
    assert EMAIL in everything_stored(setup, but_the_audit=True)
    return run_id


def test_an_old_run_is_shown_masked_before_mask_pii_is_run(setup: Setup) -> None:
    run_id = stored_before(setup)
    response = setup.client().get(f"/api/runs/{run_id}")
    assert EMAIL not in response.text
    [answer] = payment_of(response.json())["evidence"]["rows"]
    assert answer["id"] == MASKED and answer["customer"] == MASKED
    assert answer["status"] == "captured"


def config_folder(setup: Setup) -> Path:
    folder = Path(str(setup.store.url.database)).parent / "config"
    if not folder.exists():
        shutil.copytree(EXAMPLES, folder)
    return folder


def owl_mask(setup: Setup, *more: str) -> Any:
    url = str(setup.store.url.render_as_string(hide_password=False))
    return CliRunner().invoke(
        cli.app, ["store", "mask-pii", str(config_folder(setup)), "--url", url, *more]
    )


def test_mask_pii_masks_unexposed_rest_fields_in_stored_runs_only_with_apply(
    setup: Setup,
) -> None:
    run_id = stored_before(setup)
    dry = owl_mask(setup)
    assert dry.exit_code == 0, dry.output
    assert "payment evidence" in dry.output and "expectations" in dry.output
    assert EMAIL not in dry.output and "Nothing written" in dry.output
    assert EMAIL in everything_stored(setup, but_the_audit=True)
    applied = owl_mask(setup, "--apply")
    assert applied.exit_code == 0, applied.output
    assert EMAIL not in everything_stored(setup, but_the_audit=True)
    with setup.store.connect() as conn:
        run = get_run(conn, LOCAL_WORKSPACE, run_id)
    [payment] = [r for r in run.locations if r.location == "payment"]
    assert payment.evidence is not None and payment.evidence.rows[0]["id"] == MASKED
    assert run.expectations[0].detail == f"'{MASKED}' == 'x'"
    assert "Nothing to mask." in owl_mask(setup, "--apply").output


# --- owl check ----------------------------------------------------------------------------------


def _topup() -> Entity:
    return load_applications(EXAMPLES)["wallet"].entities["topup"]


def warned(tmp_path: Path, edit: dict[str, tuple[str, str]]) -> list[str]:
    """unexposed_warnings for the examples, with each file's text replaced as given."""
    folder = tmp_path / "config"
    shutil.copytree(EXAMPLES, folder)
    for name, (old, new) in edit.items():
        path = folder / name
        text = path.read_text(encoding="utf-8")
        assert old in text, (name, old)
        path.write_text(text.replace(old, new), encoding="utf-8")
    return [w for app in load_applications(folder).values() for w in unexposed_warnings(app)]


def test_the_examples_need_no_warning(tmp_path: Path) -> None:
    assert warned(tmp_path, {}) == []


def test_owl_check_names_a_compare_field_and_a_probes_read_that_are_now_masked(
    tmp_path: Path,
) -> None:
    found = warned(
        tmp_path,
        {
            "wallet/entities/topup.yaml": (
                "compare: [status, amount, currency]",
                "compare: [status, amount, currency, risk.score]",
            ),
            "wallet/probes/topup_completed.yaml": (
                "payment.status != 'captured'",
                "payment.reference != 'x'",
            ),
        },
    )
    assert found == [
        "wallet: topup.payment is a REST location, which shows only the fields it exposes; "
        "masked: reference (probe topup_completed shows it masked), "
        "risk.score (compared by fingerprint); "
        "add them to exposes to show them, or to mask to keep them masked"
    ]


def test_listing_a_field_in_mask_or_exposes_says_it_is_meant(tmp_path: Path) -> None:
    assert warned(
        tmp_path,
        {
            "wallet/entities/topup.yaml": (
                "    mask: [customer.email, customer.phone]\n"
                "    compare: [status, amount, currency]",
                "    mask: [customer.email, customer.phone, risk]\n"
                "    compare: [status, amount, currency, risk.score, id]",
            ),
        },
    ) == [
        "wallet: topup.payment is a REST location, which shows only the fields it exposes; "
        "masked: id (compared by fingerprint); "
        "add them to exposes to show them, or to mask to keep them masked"
    ]


def test_a_fan_outs_read_inside_all_is_named(tmp_path: Path) -> None:
    [warning] = warned(
        tmp_path,
        {
            "card-access/probes/visitor_cards.yaml": (
                "controller.status == status",
                "controller.door_group == status",
            )
        },
    )
    assert "visitor.controller is a REST location" in warning
    assert "door_group (probe visitor_cards shows it masked)" in warning


def test_owl_check_prints_the_warning_and_still_passes(tmp_path: Path) -> None:
    folder = tmp_path / "config"
    shutil.copytree(EXAMPLES, folder)
    path = folder / "wallet" / "entities" / "topup.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("compare: [status, amount, currency]", "compare: [status, amount, id]"),
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli.app, ["check", str(folder), "--no-connect"])
    assert result.exit_code == 0, result.output
    assert "warning: wallet: topup.payment is a REST location" in result.output
    assert "id (compared by fingerprint)" in result.output


def test_fields_used_reads_bare_fields_inside_a_set_function_as_that_locations() -> None:
    node = parse("all(cards, controller.status == status) and sum(ledger.amount) > record.x")
    assert fields_used(node) == {
        ("controller", "status"),
        ("cards", "status"),
        ("ledger", "amount"),
        ("record", "x"),
    }
