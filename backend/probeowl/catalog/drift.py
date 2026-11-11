# Author: @SaadZarook
"""Drift: where the catalog, or another environment, differs from an environment's live schema."""

from probeowl.model.catalog import CatalogTable, ColumnRole
from probeowl.model.review import Drift
from probeowl.model.schema import TableSchema


def catalog_drift(entry: CatalogTable, environment: str, live: TableSchema | None) -> list[Drift]:
    """How the confirmed catalog entry differs from the table imported in one environment."""
    if live is None:
        return [Drift(environment=environment, message="not in the live schema")]
    columns = {c.name.lower() for c in live.columns}
    found = [
        Drift(environment=environment, message=f"column {name} is not in the live schema")
        for name in entry.columns
        if name.lower() not in columns
    ]
    marked_key = [n.lower() for n, c in entry.columns.items() if ColumnRole.PRIMARY_KEY in c.roles]
    live_key = [n.lower() for n in live.primary_key]
    if live_key and sorted(marked_key) != sorted(live_key):
        found.append(
            Drift(
                environment=environment,
                message=f"primary key is ({', '.join(marked_key)}) in the catalog "
                f"but ({', '.join(live_key)}) in the live schema",
            )
        )
    leading = {index.columns[0].lower() for index in live.indexes}
    for name, column in entry.columns.items():
        if column.indexed and name.lower() in columns and name.lower() not in leading:
            found.append(
                Drift(
                    environment=environment,
                    message=f"{name} is marked indexed, but no index starts with it",
                )
            )
    return found


def environment_drift(imports: dict[str, TableSchema]) -> list[Drift]:
    """Columns missing or typed differently in an environment, against the first one imported."""
    if len(imports) < 2:
        return []
    (first, base), *others = imports.items()
    types = {c.name.lower(): c.type for c in base.columns}
    found = []
    for environment, table in others:
        theirs = {c.name.lower(): c.type for c in table.columns}
        for name, type_ in types.items():
            if name not in theirs:
                found.append(
                    Drift(
                        environment=environment, message=f"column {name} is missing; {first} has it"
                    )
                )
            elif theirs[name] != type_:
                found.append(
                    Drift(
                        environment=environment,
                        message=f"{name} is {theirs[name]} here but {type_} in {first}",
                    )
                )
        found.extend(
            Drift(environment=environment, message=f"column {name} is not in {first}")
            for name in theirs
            if name not in types
        )
    return found
