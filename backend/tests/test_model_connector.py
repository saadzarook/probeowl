# Author: @SaadZarook
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from probeowl.model import Application, Connector, RestConnector, SqlConnector

connector_adapter: TypeAdapter[Connector] = TypeAdapter(Connector)


def wallet_db() -> dict[str, Any]:
    return {
        "connector": "wallet_db",
        "type": "sqlserver",
        "environments": {
            "prod": {
                "host": "wallet-sql-ro",
                "database": "wallet",
                "user": "probeowl_ro",
                "secret": "keyring:probeowl/wallet-prod",
                "replica": True,
            },
            "uat": {"host": "wallet-sql-uat", "user": "probeowl_ro", "secret": "env:WALLET_UAT_PW"},
        },
    }


def test_sql_connector_loads() -> None:
    connector = connector_adapter.validate_python(wallet_db())
    assert isinstance(connector, SqlConnector)
    assert connector.environments["prod"].replica
    assert not connector.environments["uat"].replica


def test_rest_connector_loads() -> None:
    connector = connector_adapter.validate_python(
        {
            "connector": "checkout_api",
            "type": "rest",
            "environments": {"uat": {"base_url": "http://127.0.0.1:8081"}},
        }
    )
    assert isinstance(connector, RestConnector)
    assert connector.environments["uat"].secret is None


def test_vault_reference_is_accepted_in_the_schema() -> None:
    data = wallet_db()
    data["environments"]["prod"]["secret"] = "vault://prod/wallet-ro"
    connector_adapter.validate_python(data)


@pytest.mark.parametrize(
    "secret", ["hunter2", "keyring:probeowl", "env:", "env:WALLET PW", "vault://"]
)
def test_secret_must_be_a_reference_not_a_value(secret: str) -> None:
    data = wallet_db()
    data["environments"]["prod"]["secret"] = secret
    with pytest.raises(ValidationError, match="secret"):
        connector_adapter.validate_python(data)


def test_unknown_connector_type_is_rejected() -> None:
    data = wallet_db() | {"type": "postgres"}
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        connector_adapter.validate_python(data)


def test_sql_binding_fields_are_not_accepted_on_rest() -> None:
    data = {
        "connector": "checkout_api",
        "type": "rest",
        "environments": {"uat": {"base_url": "http://127.0.0.1:8081", "host": "x"}},
    }
    with pytest.raises(ValidationError, match="host"):
        connector_adapter.validate_python(data)


def test_application_marks_production_environment() -> None:
    app = Application.model_validate(
        {
            "application": "wallet",
            "title": "Wallet",
            "owner": "payments-team",
            "environments": {
                "prod": {"title": "Production", "production": True},
                "uat": {"title": "UAT"},
            },
        }
    )
    assert app.environments["prod"].production
    assert not app.environments["uat"].production


def test_application_needs_an_environment() -> None:
    with pytest.raises(ValidationError, match="environments"):
        Application.model_validate(
            {
                "application": "wallet",
                "title": "Wallet",
                "owner": "payments-team",
                "environments": {},
            }
        )


@pytest.mark.parametrize("name", ["Wallet", "card-access", "wallet.db", "1wallet"])
def test_names_are_lowercase_identifiers(name: str) -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        Application.model_validate(
            {
                "application": name,
                "title": "Wallet",
                "owner": "payments-team",
                "environments": {"prod": {"title": "Production"}},
            }
        )


def test_oracle_binding_sets_a_schema() -> None:
    connector = connector_adapter.validate_python(
        {
            "connector": "ledger_db",
            "type": "oracle",
            "environments": {
                "uat": {
                    "host": "127.0.0.1",
                    "database": "FREEPDB1",
                    "schema": "LEDGER_UAT",
                    "user": "probeowl_ro",
                    "secret": "env:PROBEOWL_LEDGER_DB_UAT",
                }
            },
        }
    )
    assert isinstance(connector, SqlConnector)
    assert connector.environments["uat"].db_schema == "LEDGER_UAT"


def test_schema_is_rejected_outside_oracle() -> None:
    data = wallet_db()
    data["environments"]["uat"]["schema"] = "uat"
    with pytest.raises(ValidationError, match="uat: schema applies only to oracle"):
        connector_adapter.validate_python(data)


def test_a_binding_allows_its_own_database_and_the_listed_ones() -> None:
    data = wallet_db()
    data["environments"]["prod"]["allowed_databases"] = ["wallet_archive"]
    connector = connector_adapter.validate_python(data)
    assert isinstance(connector, SqlConnector)
    assert connector.environments["prod"].databases_allowed() == {"wallet", "wallet_archive"}
    assert connector.environments["uat"].databases_allowed() == set()


def test_allowed_databases_does_not_apply_to_oracle() -> None:
    with pytest.raises(ValidationError, match="allowed_databases does not apply to oracle"):
        connector_adapter.validate_python(
            {
                "connector": "ledger_db",
                "type": "oracle",
                "environments": {
                    "uat": {
                        "host": "127.0.0.1",
                        "user": "probeowl_ro",
                        "secret": "env:PW",
                        "allowed_databases": ["other"],
                    }
                },
            }
        )


@pytest.mark.parametrize("dialect", ["sqlserver", "oracle", "mariadb"])
def test_trust_server_certificate_applies_to_every_sql_dialect(dialect: str) -> None:
    data = wallet_db() | {"type": dialect}
    data["environments"]["uat"]["trust_server_certificate"] = True
    connector = connector_adapter.validate_python(data)
    assert isinstance(connector, SqlConnector)
    assert connector.environments["uat"].trust_server_certificate


@pytest.mark.parametrize("dialect", ["sqlserver", "oracle", "mariadb"])
def test_every_sql_binding_is_encrypted_and_verified_by_default(dialect: str) -> None:
    connector = connector_adapter.validate_python(wallet_db() | {"type": dialect})
    assert isinstance(connector, SqlConnector)
    for binding in connector.environments.values():
        assert binding.encrypt is True
        assert binding.trust_server_certificate is False


@pytest.mark.parametrize("dialect", ["oracle", "mariadb"])
def test_oracle_and_mariadb_may_turn_encryption_off(dialect: str) -> None:
    data = wallet_db() | {"type": dialect}
    data["environments"]["uat"] |= {"encrypt": False, "encrypt_reason": "local container"}
    connector = connector_adapter.validate_python(data)
    assert isinstance(connector, SqlConnector)
    assert connector.environments["uat"].encrypt is False


def test_sqlserver_is_always_encrypted() -> None:
    data = wallet_db()
    data["environments"]["uat"]["encrypt"] = False
    with pytest.raises(ValidationError, match="sqlserver connections are always encrypted"):
        connector_adapter.validate_python(data)


def test_an_encrypt_reason_needs_encrypt_false() -> None:
    data = wallet_db() | {"type": "mariadb"}
    data["environments"]["uat"]["encrypt_reason"] = "local container"
    with pytest.raises(ValidationError, match="encrypt_reason needs encrypt: false"):
        connector_adapter.validate_python(data)


def test_trusting_a_certificate_needs_an_encrypted_connection() -> None:
    data = wallet_db() | {"type": "mariadb"}
    data["environments"]["uat"] |= {"encrypt": False, "trust_server_certificate": True}
    with pytest.raises(ValidationError, match="trust_server_certificate needs an encrypted"):
        connector_adapter.validate_python(data)


def test_a_trust_reason_needs_trust_server_certificate() -> None:
    data = wallet_db()
    data["environments"]["prod"]["trust_server_certificate_reason"] = "self-signed test server"
    with pytest.raises(ValidationError, match="reason needs trust_server_certificate: true"):
        connector_adapter.validate_python(data)


def test_trust_server_certificate_defaults_to_false() -> None:
    connector = connector_adapter.validate_python(wallet_db())
    assert isinstance(connector, SqlConnector)
    assert connector.environments["prod"].trust_server_certificate is False
