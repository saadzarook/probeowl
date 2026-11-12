# Author: @SaadZarook
"""Shared fixtures: the API over a real SQLite store, the examples and fake connectors."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from probeowl import secrets
from probeowl.api.app import create_app
from probeowl.guards.breaker import CircuitBreaker
from probeowl.model.check import LoadedApplication, load_applications
from probeowl.model.connector import RestConnector, SqlConnector
from probeowl.runs import RunLimits, RunService
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade
from tests.card_fakes import card_connectors
from tests.local_client import local_client
from tests.plan_fakes import Plans
from tests.wallet_fakes import Fake, ledger, payment, record, webhook

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


class MemoryKeychain:
    """Stands in for this machine's keychain: an entry exists until a test says otherwise."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}
        self.absent: set[tuple[str, str]] = set()  # entries a test removed or never had
        self.every_entry_saved = True  # unless a test sets up the keychain it wants

    def read(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def saved(self, ref: str) -> bool:
        entry = secrets.keychain_entry(ref)
        if entry is None or entry in self.entries:
            return True
        return self.every_entry_saved and entry not in self.absent

    def save(self, service: str, username: str, value: str) -> None:
        self.entries[(service, username)] = value
        self.absent.discard((service, username))

    def delete(self, service: str, username: str) -> None:
        self.entries.pop((service, username), None)
        self.absent.add((service, username))


@pytest.fixture(autouse=True)
def machine_keychain(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> MemoryKeychain:
    """No test reads or writes this machine's keychain; test_secrets tests the real functions."""
    memory = MemoryKeychain()
    if request.module.__name__.endswith("test_secrets"):
        return memory
    monkeypatch.setattr(secrets, "secret_saved", memory.saved)
    monkeypatch.setattr(secrets, "read_keychain", memory.read)
    monkeypatch.setattr(secrets, "save_secret", memory.save)
    monkeypatch.setattr(secrets, "delete_keychain", memory.delete)
    return memory


@dataclass
class Setup:
    """The API, its store and the fake connectors a test can change before starting runs."""

    store: Engine
    applications: dict[str, LoadedApplication]
    fakes: dict[str, Fake]
    unbound: set[str]
    breaker: CircuitBreaker | None = None  # shared across services when a test sets one
    plans: Plans = field(default_factory=Plans)  # the plans EXPLAIN would return

    def service(self, **limits: Any) -> RunService:
        def opener(connector: SqlConnector | RestConnector, environment: str) -> Fake | None:
            if connector.connector in self.unbound or environment not in connector.environments:
                return None
            return self.fakes[connector.connector]

        return RunService(
            self.applications,
            self.store,
            RunLimits(**limits),
            opener,
            actor_name="s.khan",
            breaker=self.breaker,
        )

    def client(self, **limits: Any) -> TestClient:
        return local_client(create_app(self.service(**limits), plans=self.plans))


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[Setup]:
    store = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    upgrade(store)

    def wallet_db(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        return webhook(lookup, p) if "provider_webhooks" in lookup else record(lookup, p)

    fakes = {
        "wallet_db": Fake(wallet_db),
        "checkout_api": Fake(payment),
        "ledger_db": Fake(ledger),
        **card_connectors(),
    }
    yield Setup(store, load_applications(EXAMPLES), fakes, set())
    store.dispose()
