# Author: @SaadZarook
"""The exit checks' leak scanner finds a masked value, however written, and never prints it."""

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from tests.conftest import EXAMPLES

SCANNER = Path(__file__).resolve().parents[1] / "ci" / "scan_responses.py"


@pytest.fixture
def scanner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scan_responses", SCANNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("text", "leaks"),
    [
        ('"query":"GET /payments/pay_7hq2m9"', True),
        ('"rows":[{"id":"PAY_7HQ2M9"}],"rows_total":null', True),
        ('"query":"GET /payments/[masked]"', False),
    ],
)
def test_a_masked_value_is_found_in_a_response_result_rows_included(
    scanner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    text: str,
    leaks: bool,
) -> None:
    real = httpx.Client

    def client(**given: object) -> httpx.Client:
        answer = httpx.MockTransport(
            lambda r: httpx.Response(200, text=text if r.url.path == "/api/runs/r1" else "{}")
        )
        return real(base_url="http://owl", transport=answer)

    monkeypatch.setattr(scanner.httpx, "Client", client)
    assert scanner.main(EXAMPLES, "http://owl", ["r1"], ["pay_7hq2m9"]) == int(leaks)
    # What it prints is a log too.
    printed = capsys.readouterr()
    assert "pay_7hq2m9" not in (printed.out + printed.err).casefold()


@pytest.mark.parametrize("written", ["someone@example.com", "someone%40example.com"])
def test_a_masked_value_is_found_in_a_log_as_it_is_or_encoded(
    scanner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, written: str
) -> None:
    real = httpx.Client
    monkeypatch.setattr(
        scanner.httpx,
        "Client",
        lambda **_: real(
            base_url="http://owl",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="{}")),
        ),
    )
    log = tmp_path / "owl-serve.log"
    log.write_text(f"GET /api/search?q={written}", encoding="utf-8")
    assert scanner.main(EXAMPLES, "http://owl", [], ["someone@example.com"], [log]) == 1
    log.write_text("GET /api/search", encoding="utf-8")
    assert scanner.main(EXAMPLES, "http://owl", [], ["someone@example.com"], [log]) == 0
