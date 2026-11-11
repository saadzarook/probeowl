# Author: @SaadZarook
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from probeowl.cli import main as cli
from probeowl.connectors.failures import driver_log_text, show_raw_driver_errors
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import check_current

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
runner = CliRunner()


def owl(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr("sys.argv", ["owl", *args])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    code = exit_info.value.code
    return code if isinstance(code, int) else 0


@pytest.fixture
def wallet(tmp_path: Path) -> Path:
    shutil.copytree(EXAMPLES / "wallet", tmp_path / "wallet")
    return tmp_path


def test_check_passes_on_the_examples() -> None:
    result = owl("check", str(EXAMPLES))
    assert result.exit_code == 0, result.output
    assert "OK: 14 files in 2 applications" in result.output
    # owl check reads no schema imports; the healthy sample compares wallet_db's time column.
    assert "warning: wallet: time-offset check not run: no schema import for wallet_db" in (
        result.output
    )


def test_production_binding_without_replica_is_an_error(wallet: Path) -> None:
    path = wallet / "wallet" / "connectors" / "wallet_db.yaml"
    path.write_text(path.read_text().replace("replica: true", "replica: false"))
    result = owl("check", str(wallet))
    assert result.exit_code == 1
    assert "the prod binding is Production and must use a replica (replica: true)" in result.output


def test_unknown_environment_in_a_binding_is_an_error(wallet: Path) -> None:
    path = wallet / "wallet" / "connectors" / "checkout_api.yaml"
    path.write_text(path.read_text().replace("  uat:", "  staging:"))
    result = owl("check", str(wallet))
    assert result.exit_code == 1
    assert "environment staging is not in application.yaml" in result.output


def test_probe_naming_an_unknown_entity_is_an_error(wallet: Path) -> None:
    path = wallet / "wallet" / "probes" / "topup_completed.yaml"
    path.write_text(path.read_text().replace("entity: topup", "entity: refund"))
    result = owl("check", str(wallet))
    assert result.exit_code == 1
    assert "the probe uses entity refund, which is not defined" in result.output


def test_location_using_an_unknown_connector_is_an_error(wallet: Path) -> None:
    path = wallet / "wallet" / "entities" / "topup.yaml"
    path.write_text(path.read_text().replace("using: ledger_db", "using: ledger_api"))
    result = owl("check", str(wallet))
    assert "location ledger uses connector ledger_api, which is not defined" in result.output


def test_runbook_references_are_checked(wallet: Path) -> None:
    path = wallet / "wallet" / "runbooks" / "topup_not_credited.yaml"
    path.write_text(path.read_text().replace("probe: topup_completed", "probe: topup_done"))
    result = owl("check", str(wallet))
    assert "uses probe topup_done, which is not defined" in result.output


def test_every_invalid_file_is_reported_not_only_the_first(wallet: Path) -> None:
    (wallet / "wallet" / "probes" / "a.yaml").write_text("probe: [unclosed\n")
    (wallet / "wallet" / "probes" / "b.yaml").write_text("probe: b\nkind: inspect\n")
    result = owl("check", str(wallet))
    assert result.exit_code == 1
    assert "a.yaml: not valid YAML" in result.output
    assert "b.yaml: invalid probe" in result.output


def test_duplicate_names_are_an_error(wallet: Path) -> None:
    probes = wallet / "wallet" / "probes"
    shutil.copy(probes / "topup_completed.yaml", probes / "copy.yaml")
    result = owl("check", str(wallet))
    assert "topup_completed is defined twice in this application" in result.output


def test_file_outside_an_application_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "stray.yaml").write_text(
        "probe: p\nkind: inspect\nentity: e\nowner: t\nexpect: count(x) >= 1\n"
    )
    result = owl("check", str(tmp_path))
    assert "not inside an application folder" in result.output


def test_check_of_a_missing_folder_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_main(monkeypatch, "check", str(tmp_path / "nope")) == 1
    assert "is not a folder" in capsys.readouterr().err


def test_store_init_creates_a_sqlite_store_and_can_run_again(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'deep' / 'store.db'}"
    for _ in range(2):
        result = owl("store", "init", "--url", url)
        assert result.exit_code == 0, result.output
    engine = create_store_engine(url)
    check_current(engine)
    engine.dispose()


def test_store_init_uses_the_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROBEOWL_STORE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    assert owl("store", "init").exit_code == 0
    assert (tmp_path / "env.db").exists()


class _NoEngine:
    def dispose(self) -> None:
        pass


def test_store_init_hides_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    # No PostgreSQL here: only the message is under test.
    monkeypatch.setattr(cli, "create_store_engine", lambda url: _NoEngine())
    monkeypatch.setattr(cli.migrate, "upgrade", lambda engine: None)
    result = owl("store", "init", "--url", "postgresql+psycopg://owl:s3cret@db.example/probeowl")
    assert result.exit_code == 0, result.output
    assert "s3cret" not in result.output


def test_store_init_refuses_sqlite_on_a_network_share(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_main(monkeypatch, "store", "init", "--url", "sqlite://///fileserver/team/store.db")
    assert code == 1
    assert "network share" in capsys.readouterr().err


def test_store_ddl_prints_sql_for_a_dialect() -> None:
    result = owl("store", "ddl", "--dialect", "oracle")
    assert result.exit_code == 0
    assert "CREATE TABLE audit_events" in result.output


def test_store_ddl_unknown_dialect_is_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_main(monkeypatch, "store", "ddl", "--dialect", "db2") == 1
    assert "unknown store dialect 'db2'" in capsys.readouterr().err


def ready_store(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    assert owl("store", "init", "--url", url).exit_code == 0
    return url


@pytest.fixture(autouse=True)
def log_in_a_temporary_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """owl serve must not write to the home folder of whoever runs the tests."""
    path = tmp_path / "logs" / "owl-serve.log"
    monkeypatch.setattr(cli, "default_log_file", lambda: path)
    monkeypatch.setattr(cli, "default_settings_file", lambda: tmp_path / "plugins.yaml")
    yield path
    show_raw_driver_errors(False)
    logger = logging.getLogger("probeowl")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_debug_driver_errors_is_off_unless_asked_for_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log_in_a_temporary_folder: Path
) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    url = ready_store(tmp_path)
    quiet = owl("serve", "--config", str(EXAMPLES), "--url", url)
    assert quiet.exit_code == 0 and "debug-driver-errors" not in quiet.output
    assert log_in_a_temporary_folder.exists()
    assert driver_log_text("value 'x'", "value 'x'", "pw") == "value [masked]"
    loud = owl("serve", "--config", str(EXAMPLES), "--url", url, "--debug-driver-errors")
    assert loud.exit_code == 0
    assert "warning: --debug-driver-errors is on" in loud.output
    assert driver_log_text("value 'x'", "value 'x'", "pw") == "value 'x'"


def test_serve_binds_to_localhost_with_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    served: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: served.append((app, kwargs)))
    url = ready_store(tmp_path)
    result = owl("serve", "--port", "7979", "--config", str(EXAMPLES), "--url", url)
    assert result.exit_code == 0, result.output
    [(app, kwargs)] = served
    assert kwargs == {"host": "127.0.0.1", "port": 7979}
    assert app.state.runs is not None


def test_serve_takes_the_config_folder_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    monkeypatch.setenv("PROBEOWL_CONFIG", str(EXAMPLES))
    assert owl("serve", "--url", ready_store(tmp_path)).exit_code == 0


def test_serve_refuses_a_store_that_is_not_initialised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    assert run_main(monkeypatch, "serve", "--config", str(EXAMPLES), "--url", url) == 1
    assert "run: owl store init" in capsys.readouterr().err


def test_serve_refuses_probe_files_that_do_not_pass_owl_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wallet: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    probe = wallet / "wallet" / "probes" / "topup_completed.yaml"
    probe.write_text(probe.read_text().replace("entity: topup", "entity: refund"))
    url = ready_store(tmp_path / "s")
    assert run_main(monkeypatch, "serve", "--config", str(wallet), "--url", url) == 1
    assert "run: owl check" in capsys.readouterr().err


def test_serve_has_no_host_option() -> None:
    assert owl("serve", "--host", "0.0.0.0").exit_code == 2


def _trust_in_prod(wallet: Path, reason: str | None) -> Any:
    path = wallet / "wallet" / "connectors" / "wallet_db.yaml"
    lines = [
        line for line in path.read_text().splitlines() if "trust_server_certificate" not in line
    ]
    text = "\n".join(lines) + "\n"
    extra = "    trust_server_certificate: true\n"
    if reason is not None:
        extra += f"    trust_server_certificate_reason: {reason}\n"
    path.write_text(text.replace("    replica: true\n", "    replica: true\n" + extra))
    return owl("check", str(wallet))


def test_production_trusting_any_certificate_without_a_reason_is_an_error(wallet: Path) -> None:
    result = _trust_in_prod(wallet, reason=None)
    assert result.exit_code == 1
    assert "the prod binding is Production and trusts any server certificate" in result.output


def test_production_trusting_any_certificate_with_a_reason_passes(wallet: Path) -> None:
    result = _trust_in_prod(wallet, reason="vendor appliance with a self-signed certificate")
    assert result.exit_code == 0, result.output


def _unencrypted_in_prod(wallet: Path, reason: str | None) -> Any:
    path = wallet / "wallet" / "connectors" / "ledger_db.yaml"
    lines = [line for line in path.read_text().splitlines() if "encrypt" not in line]
    text = "\n".join(lines) + "\n"
    extra = "    encrypt: false\n"
    if reason is not None:
        extra += f"    encrypt_reason: {reason}\n"
    path.write_text(text.replace("    replica: true\n", "    replica: true\n" + extra))
    return owl("check", str(wallet))


def test_production_without_encryption_and_without_a_reason_is_an_error(wallet: Path) -> None:
    result = _unencrypted_in_prod(wallet, reason=None)
    assert result.exit_code == 1
    assert "the prod binding is Production and is not encrypted" in result.output
    assert "encrypt_reason" in result.output


def test_production_without_encryption_with_a_reason_passes(wallet: Path) -> None:
    result = _unencrypted_in_prod(wallet, reason="appliance with no TLS listener")
    assert result.exit_code == 0, result.output


def test_every_example_binding_that_lowers_transport_security_says_why() -> None:
    # Local dev bindings turn encryption or verification off, always with a reason.
    for path in sorted(EXAMPLES.glob("*/connectors/*.yaml")):
        document = yaml.safe_load(path.read_text())
        for name, binding in document["environments"].items():
            if binding.get("encrypt") is False:
                assert binding.get("encrypt_reason"), f"{path.name} {name}"
            if binding.get("trust_server_certificate"):
                assert binding.get("trust_server_certificate_reason"), f"{path.name} {name}"


def test_a_probe_expression_that_cannot_run_is_an_error(wallet: Path) -> None:
    path = wallet / "wallet" / "probes" / "topup_completed.yaml"
    path.write_text(path.read_text().replace("count(webhook) >= 1", "count(balance) >= 1"))
    result = owl("check", str(wallet))
    assert result.exit_code == 1
    assert "reads balance, not a location of topup" in result.output


def test_an_expression_that_cannot_be_read_is_an_error(wallet: Path) -> None:
    path = wallet / "wallet" / "probes" / "topup_completed.yaml"
    path.write_text(path.read_text().replace("count(webhook) >= 1", "count(webhook) >="))
    assert "cannot read 'count(webhook) >='" in owl("check", str(wallet)).output


def test_pii_on_a_rest_location_and_mask_on_a_sql_location_are_errors(wallet: Path) -> None:
    path = wallet / "wallet" / "entities" / "topup.yaml"
    text = path.read_text()
    text = text.replace(
        "    exposes: [status, amount, currency]",
        "    exposes: [status, amount, currency]\n    pii: [card_holder]",
    )
    text = text.replace("    pii: [customer_email]", "    mask: [customer_email]")
    path.write_text(text)
    output = owl("check", str(wallet)).output
    assert "location payment is a REST location: list its fields in mask, not pii" in output
    assert "location record is a SQL location: list its columns in pii, not mask" in output


def test_a_probe_enabled_where_a_location_has_no_binding_is_an_error(wallet: Path) -> None:
    app = wallet / "wallet" / "application.yaml"
    app.write_text(app.read_text() + "  dev: { title: Dev }\n")
    # Every connector but ledger_db gets a dev binding.
    for name in ("wallet_db", "checkout_api"):
        path = wallet / "wallet" / "connectors" / f"{name}.yaml"
        text = path.read_text()
        uat = text[text.index("  uat:") :]
        path.write_text(text + uat.replace("  uat:", "  dev:", 1))
    result = owl("check", str(wallet))
    assert result.exit_code == 1
    assert (
        "probe topup_completed is enabled in dev, where location ledger has no binding (ledger_db)"
        in result.output
    )
    # Leaving dev out of the probe's environments is the way to say so.
    probe = wallet / "wallet" / "probes" / "topup_completed.yaml"
    probe.write_text(
        probe.read_text().replace(
            "owner: payments-team", "owner: payments-team\nenvironments: [prod, uat]"
        )
    )
    assert "enabled in dev" not in owl("check", str(wallet)).output


def test_a_probe_enabled_in_an_unknown_environment_is_an_error(wallet: Path) -> None:
    probe = wallet / "wallet" / "probes" / "topup_completed.yaml"
    probe.write_text(
        probe.read_text().replace(
            "owner: payments-team", "owner: payments-team\nenvironments: [prod, staging]"
        )
    )
    assert (
        "probe topup_completed is enabled in staging, not an environment here"
        in owl("check", str(wallet)).output
    )


def test_an_identifier_whose_lookup_cannot_be_rewritten_is_an_error(wallet: Path) -> None:
    path = wallet / "wallet" / "entities" / "topup.yaml"
    path.write_text(
        path.read_text().replace(
            "FROM topups WHERE id = :topup_id", "FROM topups WHERE id = :topup_id OR id = 'x'"
        )
    )
    result = owl("check", str(wallet))
    assert result.exit_code == 1
    assert "identifier provider_payment_id via record.provider_payment_id" in result.output
    assert "exactly one condition <column> = :topup_id" in result.output


@pytest.mark.parametrize(
    ("before", "after", "message"),
    [
        (
            "WHERE txn_id = :ledger_txn_id",
            "WHERE account = :ledger_txn_id",
            "ledger_entries is a Transaction table: filter on its time column posted_at",
        ),
        ("pii: [customer_email]", "pii: []", "customer_email"),
        (
            "SELECT account, direction, amount, posted_at",
            "DELETE",
            "topup.ledger",
        ),
    ],
    ids=["time filter", "PII masking", "guard"],
)
def test_check_runs_the_save_checks_that_need_no_database(
    wallet: Path, before: str, after: str, message: str
) -> None:
    path = wallet / "wallet" / "entities" / "topup.yaml"
    text = path.read_text(encoding="utf-8")
    assert before in text
    path.write_text(text.replace(before, after), encoding="utf-8")
    result = owl("check", str(wallet), "--no-connect")
    assert result.exit_code == 1, result.output
    assert message in result.output
    assert "OK:" not in result.output


def test_check_needs_no_database_for_the_save_checks(
    wallet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("no connector is opened")

    monkeypatch.setattr("probeowl.connectors.factory.open_sql", refuse)
    result = owl("check", str(wallet), "--no-connect")
    assert result.exit_code == 0, result.output


def test_version_is_the_packages() -> None:
    from importlib.metadata import version

    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert result.output == f"owl {version('probeowl')}\n"
    assert version("probeowl") == "0.1.0"


@pytest.mark.parametrize(
    ("n", "word", "said"),
    [
        (1, "application", "1 application"),
        (2, "application", "2 applications"),
        (0, "error", "0 errors"),
    ],
)
def test_counts_are_said_in_the_singular_for_one(n: int, word: str, said: str) -> None:
    assert cli.counted(n, word) == said
