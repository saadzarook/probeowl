# Author: @SaadZarook
"""Which tables each probe reads: the tables named in its entity's SQL lookups."""

from collections import defaultdict

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from probeowl.guards.dialects import RULES_BY_CONNECTOR_TYPE
from probeowl.model.check import LoadedApplication
from probeowl.model.connector import SqlConnector
from probeowl.model.entity import LocationBase

# A table as (connector, name in lower case): catalogs and imports name tables in any case.
TableRef = tuple[str, str]


def tables_used(app: LoadedApplication) -> dict[TableRef, list[str]]:
    """Each table any probe reads, with those probes' names in order."""
    by_entity: dict[str, set[TableRef]] = defaultdict(set)
    for name, entity in app.entities.items():
        for location in entity.locations.values():
            by_entity[name] |= _tables(app, location)
            for fan_out in location.each.values():
                by_entity[name] |= _tables(app, fan_out)
    used: dict[TableRef, list[str]] = defaultdict(list)
    for probe in sorted(app.probes.values(), key=lambda p: p.probe):
        for table in sorted(by_entity.get(probe.entity, set())):
            used[table].append(probe.probe)
    return dict(used)


def _tables(app: LoadedApplication, location: LocationBase) -> set[TableRef]:
    connector = app.connectors.get(location.using)
    if not isinstance(connector, SqlConnector):
        return set()
    found = set()
    for sql in (location.lookup, location.fallback):
        if sql is None:
            continue
        try:
            tree = sqlglot.parse_one(
                sql, read=RULES_BY_CONNECTOR_TYPE[connector.type].sqlglot_dialect
            )
        except ParseError:
            continue  # owl check reports a lookup that cannot be read
        # Names a WITH clause defines are not tables.
        ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        found |= {
            (location.using, table.name.lower())
            for table in tree.find_all(exp.Table)
            if table.name.lower() not in ctes
        }
    return found
