# Author: @SaadZarook
import pytest

from tests.store_databases import check_test_database, store_database_url, url_variable


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://owl:pw@db.example/probeowl_test",
        "mssql+mssqlpython://owl:pw@db.example:1433/TestStore",
        "mariadb+pymysql://owl:pw@db.example/test",
        "oracle+oracledb://probeowl_test:pw@db.example:1521/?service_name=FREEPDB1",
        "oracle+oracledb://owl:pw@db.example:1521/?service_name=TESTPDB",
    ],
)
def test_test_databases_are_accepted(url: str) -> None:
    check_test_database(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://owl:pw@db.example/probeowl",
        "mssql+mssqlpython://owl:pw@db.example:1433/master",
        "oracle+oracledb://probeowl:pw@db.example:1521/?service_name=FREEPDB1",
    ],
)
def test_other_databases_are_refused_without_showing_the_password(url: str) -> None:
    with pytest.raises(pytest.fail.Exception, match="must contain 'test'") as failure:
        check_test_database(url)
    assert ":pw@" not in str(failure.value)


def test_url_variable_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "postgresql+psycopg://owl:pw@db.example/probeowl_test"
    monkeypatch.setenv(url_variable("postgresql"), url)
    with store_database_url("postgresql") as found:
        assert found == url


def test_skips_outside_ci_without_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(url_variable("oracle"), raising=False)
    monkeypatch.delenv("CI", raising=False)
    with (
        pytest.raises(
            pytest.skip.Exception,
            match="PROBEOWL_TEST_ORACLE_URL is not set, so the database tests",
        ),
        store_database_url("oracle"),
    ):
        pass
