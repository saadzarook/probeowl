# Author: @SaadZarook
"""Fake plan sources: what EXPLAIN would answer, without a database."""

from collections.abc import Callable, Mapping

from probeowl.checks.plans import PlanSource
from probeowl.connectors.plan import Access, PlanStep
from probeowl.model.connector import SqlConnector

Planner = Callable[[str], list[PlanStep]]


def index_seek(sql: str) -> list[PlanStep]:
    """Every lookup seeks an index: the plans of a well-indexed database."""
    return [PlanStep("Index Seek", Access.INDEX, "t", "ix_key")]


class Plans:
    """A plan source per connector, answering from a planner, recording what it was asked."""

    def __init__(self, planner: Planner = index_seek, unbound: frozenset[str] = frozenset()):
        self.planner = planner
        self.unbound = unbound
        self.asked: list[tuple[str, str, str]] = []
        self.opened: list[tuple[str, str]] = []
        self.closed = 0

    def __call__(self, connector: SqlConnector, environment: str) -> PlanSource | None:
        self.opened.append((connector.connector, environment))
        if connector.connector in self.unbound:
            return None
        plans = self

        class Source:
            def explain(
                self,
                sql: str,
                parameters: Mapping[str, object],
                parameter_types: Mapping[str, str] | None = None,
            ) -> list[PlanStep]:
                plans.asked.append((connector.connector, environment, sql))
                return plans.planner(sql)

            def close(self) -> None:
                plans.closed += 1

        return Source()
