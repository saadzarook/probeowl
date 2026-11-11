# Author: @SaadZarook
"""Shared fixtures: the API over a real SQLite store, the examples and fake connectors."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

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
