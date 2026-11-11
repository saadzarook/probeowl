# Author: @SaadZarook
"""The healthy sample in config, compare fields and the Expected to vary role."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from probeowl.checks.rules import folder_failures, statements_of
from probeowl.model.catalog import Column, ColumnRole
from probeowl.model.check import LoadedApplication, check_folder, load_applications
from probeowl.model.entity import Entity, FanOutLocation, HealthyNarrowing, HealthySample
from tests.test_entity_save import Both

EXAMPLES = Path(__file__).parents[2] / "examples"
SAMPLE = "SELECT id AS topup_id FROM topups WHERE status = 'succeeded' AND id <> :topup_id"


@pytest.fixture
def wallet() -> LoadedApplication:
    return load_applications(EXAMPLES)["wallet"]


def sample_failures(app: LoadedApplication, lookup: str, using: str = "wallet_db") -> list[str]:
    app.entities["topup"].healthy_sample = HealthySample(
        using=using, lookup=lookup, parameters={"topup_id": "record.topup_id"}
    )
    return folder_failures(app)


def entity_with(sample: dict[str, Any]) -> Entity:
    return Entity.model_validate(
        {
            "entity": "topup",
            "root_key": "topup_id",
            "locations": {
                "record": {
                    "using": "wallet_db",
                    "lookup": "SELECT id AS topup_id FROM topups WHERE id = :topup_id",
                    "exposes": ["topup_id", "email"],
                    "pii": ["email"],
                },
                "webhook": {
                    "using": "wallet_db",
                    "lookup": "SELECT id FROM provider_webhooks WHERE payment_id = :topup_id",
                    "cardinality": "many",
                    "exposes": ["id"],
                },
            },
            "healthy_sample": {"using": "wallet_db", "lookup": SAMPLE} | sample,
        }
    )


# --- The examples ---------------------------------------------------------------------------


def test_the_examples_pass_with_their_samples() -> None:
    assert check_folder(EXAMPLES).findings == []
    for app in load_applications(EXAMPLES).values():
        assert folder_failures(app) == []


def test_every_sample_lookup_gets_the_checks_a_lookup_gets(wallet: LoadedApplication) -> None:
    locations = [s.location for s in statements_of(wallet, wallet.entities["topup"])]
    assert "healthy_sample" in locations
    assert "healthy_sample.same_payment_method" in locations


# --- Where its parameters come from ---------------------------------------------------------


def test_a_parameter_needs_a_location_that_exposes_it() -> None:
    with pytest.raises(ValidationError, match="does not expose nope"):
        entity_with({"parameters": {"x": "record.nope"}})
    with pytest.raises(ValidationError, match="no location named nowhere"):
        entity_with({"parameters": {"x": "nowhere.id"}})


def test_a_parameter_cannot_come_from_a_location_with_many_rows() -> None:
    with pytest.raises(ValidationError, match="webhook can have many rows"):
        entity_with({"parameters": {"x": "webhook.id"}})


def test_a_parameter_cannot_be_masked() -> None:
    with pytest.raises(ValidationError, match=r"record\.email is masked"):
        entity_with({"parameters": {"x": "record.email"}})


# --- Its save checks -------------------------------------------------------------------------


def test_the_sample_must_select_the_root_key(wallet: LoadedApplication) -> None:
    failures = sample_failures(wallet, "SELECT id FROM topups WHERE id <> :topup_id")
    assert [f for f in failures if "must select the root key as topup_id" in f]


def test_every_parameter_must_say_where_it_comes_from(wallet: LoadedApplication) -> None:
    failures = sample_failures(wallet, f"{SAMPLE} AND created_at < :before")
    assert [f for f in failures if "say where before comes from" in f]


def test_the_sample_needs_a_sql_connector(wallet: LoadedApplication) -> None:
    failures = sample_failures(wallet, SAMPLE, using="checkout_api")
    assert [f for f in failures if "checkout_api is not a SQL connector" in f]


@pytest.mark.parametrize(
    "lookup",
    ["UPDATE topups SET status = 'x' WHERE id = :topup_id", "SELECT id FROM t WITH (NOLOCK)"],
)
def test_the_sample_must_pass_the_guard(wallet: LoadedApplication, lookup: str) -> None:
    assert [f for f in sample_failures(wallet, lookup) if "healthy_sample" in f]


def test_a_sample_on_a_transaction_table_needs_a_time_bound(wallet: LoadedApplication) -> None:
    failures = sample_failures(wallet, SAMPLE)
    assert [f for f in failures if "topups is a Transaction table" in f]


def test_a_sample_selecting_pii_is_refused(wallet: LoadedApplication) -> None:
    lookup = (
        "SELECT id AS topup_id, customer_email FROM topups "
        "WHERE id <> :topup_id AND created_at >= DATEADD(day, -7, CURRENT_TIMESTAMP)"
    )
    assert [f for f in sample_failures(wallet, lookup) if "customer_email is PII" in f]


def test_a_narrowing_gets_the_same_checks(wallet: LoadedApplication) -> None:
    sample = HealthySample(
        using="wallet_db",
        lookup=(
            "SELECT id AS topup_id FROM topups "
            "WHERE id <> :topup_id AND created_at >= DATEADD(day, -7, CURRENT_TIMESTAMP)"
        ),
        parameters={"topup_id": "record.topup_id"},
        narrow={"bad": HealthyNarrowing(title="Bad", lookup="DELETE FROM topups")},
    )
    wallet.entities["topup"].healthy_sample = sample
    assert [f for f in folder_failures(wallet) if "healthy_sample.bad" in f]


def test_a_save_that_breaks_the_sample_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    both = Both(tmp_path)
    broken = both.entity_text.replace("SELECT id AS topup_id FROM topups", "SELECT id FROM topups")
    assert broken != both.entity_text
    response = both.save_both(entity=broken)
    assert response.status_code == 422
    assert "must select the root key" in str(response.json())
    assert both.unchanged()


def test_owl_check_reports_a_sample_on_an_unknown_connector(tmp_path: Path) -> None:
    both = Both(tmp_path)
    broken = both.entity_text.replace("using: wallet_db\n  lookup", "using: nope\n  lookup")
    assert broken != both.entity_text
    both.entity_path.write_text(broken, encoding="utf-8")
    assert "healthy_sample" in str(check_folder(both.root).findings)


# --- Compare fields -------------------------------------------------------------------------


def test_expected_to_vary_must_be_a_compare_field() -> None:
    with pytest.raises(ValidationError, match="not in compare: amount"):
        FanOutLocation(
            using="checkout_api",
            lookup="GET /x",
            max_fanout=1,
            compare=["status"],
            expected_to_vary=["amount"],
        )


def test_a_sql_location_marks_compare_fields_in_the_catalog(tmp_path: Path) -> None:
    both = Both(tmp_path)
    marked = both.entity_text.replace(
        "    pii: [customer_email]\n", "    pii: [customer_email]\n    compare: [status]\n"
    )
    assert marked != both.entity_text
    both.entity_path.write_text(marked, encoding="utf-8")
    found = str(check_folder(both.root).findings)
    assert "mark its compare columns in the table catalog" in found


def test_the_catalog_role_needs_compare() -> None:
    with pytest.raises(ValidationError, match="applies only to a compare column"):
        Column(roles=[ColumnRole.EXPECTED_TO_VARY])
    assert Column(roles=[ColumnRole.COMPARE, ColumnRole.EXPECTED_TO_VARY])
