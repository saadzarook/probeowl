# Author: @SaadZarook
from typing import Any

import pytest
from pydantic import ValidationError

from probeowl.model.catalog import Catalog, ColumnRole, TableLabel


def topups(**changes: Any) -> dict[str, Any]:
    table: dict[str, Any] = {
        "connector": "wallet_db",
        "table": "topups",
        "label": "transaction",
        "owner": "payments-team",
        "columns": {
            "id": {"roles": ["primary_key"]},
            "created_at": {"roles": ["time"], "indexed": True},
            "status": {"roles": ["status", "compare"], "values": ["pending", "succeeded"]},
            "ledger_txn_id": {
                "roles": ["link"],
                "links_to": {
                    "connector": "ledger_db",
                    "table": "ledger_entries",
                    "column": "txn_id",
                },
            },
        },
    }
    return table | changes


def catalog(*tables: dict[str, Any]) -> dict[str, Any]:
    return {"catalog": "wallet", "tables": list(tables) or [topups()]}


def test_catalog_loads() -> None:
    loaded = Catalog.model_validate(catalog())
    table = loaded.tables[0]
    assert table.label is TableLabel.TRANSACTION
    assert table.columns["status"].roles == [ColumnRole.STATUS, ColumnRole.COMPARE]


@pytest.mark.parametrize("label", ["transaction", "audit", "log"])
def test_time_column_required_for_growing_tables(label: str) -> None:
    columns = topups()["columns"]
    columns.pop("created_at")
    with pytest.raises(ValidationError, match=f"a {label} table needs a time column"):
        Catalog.model_validate(catalog(topups(label=label, columns=columns)))


def test_master_table_needs_no_time_column() -> None:
    columns = topups()["columns"]
    columns.pop("created_at")
    Catalog.model_validate(catalog(topups(label="master", columns=columns)))


def test_primary_key_must_be_marked() -> None:
    columns = topups()["columns"]
    columns.pop("id")
    with pytest.raises(ValidationError, match="mark the primary key column"):
        Catalog.model_validate(catalog(topups(columns=columns)))


def test_only_one_time_column() -> None:
    columns = topups()["columns"] | {"updated_at": {"roles": ["time"]}}
    with pytest.raises(ValidationError, match="at most one time column"):
        Catalog.model_validate(catalog(topups(columns=columns)))


def test_link_needs_links_to() -> None:
    columns = topups()["columns"]
    columns["ledger_txn_id"].pop("links_to")
    with pytest.raises(ValidationError, match="links_to is required"):
        Catalog.model_validate(catalog(topups(columns=columns)))


def test_links_to_only_on_link_columns() -> None:
    columns = topups()["columns"]
    columns["id"]["links_to"] = {"connector": "checkout_api"}
    with pytest.raises(ValidationError, match="links_to is required"):
        Catalog.model_validate(catalog(topups(columns=columns)))


def test_links_to_column_needs_table() -> None:
    columns = topups()["columns"]
    columns["ledger_txn_id"]["links_to"] = {"connector": "ledger_db", "column": "txn_id"}
    with pytest.raises(ValidationError, match=r"needs links_to\.table"):
        Catalog.model_validate(catalog(topups(columns=columns)))


def test_values_only_on_status_columns() -> None:
    columns = topups()["columns"]
    columns["id"]["values"] = ["x"]
    with pytest.raises(ValidationError, match="values applies only to a status column"):
        Catalog.model_validate(catalog(topups(columns=columns)))


def test_indexed_only_on_time_columns() -> None:
    columns = topups()["columns"]
    columns["id"]["indexed"] = True
    with pytest.raises(ValidationError, match="indexed applies only to a time column"):
        Catalog.model_validate(catalog(topups(columns=columns)))


def test_table_listed_twice_is_rejected() -> None:
    with pytest.raises(ValidationError, match=r"wallet_db\.TOPUPS is listed twice"):
        Catalog.model_validate(catalog(topups(), topups(table="TOPUPS")))


def test_unknown_label_is_rejected() -> None:
    with pytest.raises(ValidationError, match="label"):
        Catalog.model_validate(catalog(topups(label="history")))
