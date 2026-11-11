# Author: @SaadZarook
"""Bring the catalog, the latest imports and probe usage together into tables to review."""

from probeowl.catalog.drift import catalog_drift, environment_drift
from probeowl.catalog.suggest import KeyTable, key_tables, suggest
from probeowl.catalog.usage import TableRef, tables_used
from probeowl.model.catalog import CatalogTable
from probeowl.model.check import LoadedApplication
from probeowl.model.review import CatalogReview, TableReview
from probeowl.model.schema import SchemaSnapshot, TableSchema


def build_review(app: LoadedApplication, snapshots: list[SchemaSnapshot]) -> CatalogReview:
    """Every table in the catalog, in an import or read by a probe, with what to review.

    snapshots: the latest import of each connector in each environment.
    """
    order = list(app.application.environments)
    imported: dict[TableRef, dict[str, TableSchema]] = {}
    for snapshot in sorted(
        snapshots,
        key=lambda s: order.index(s.environment) if s.environment in order else len(order),
    ):
        for table in snapshot.tables:
            imported.setdefault((snapshot.connector, table.table.lower()), {})[
                snapshot.environment
            ] = table
    catalog: dict[TableRef, CatalogTable] = {
        (t.connector, t.table.lower()): t for t in (app.catalog.tables if app.catalog else [])
    }
    imported_environments = {
        connector: [s.environment for s in snapshots if s.connector == connector]
        for connector in {s.connector for s in snapshots}
    }
    used = tables_used(app)
    # What a column named for a table's key can link to: every imported table, on any connector.
    keys = key_tables((ref[0], next(iter(found.values()))) for ref, found in imported.items())
    tables = [
        _review(
            ref,
            catalog.get(ref),
            imported.get(ref, {}),
            imported_environments,
            used.get(ref, []),
            keys,
        )
        for ref in sorted({*catalog, *imported, *used})
    ]
    return CatalogReview(
        application=app.application.application,
        tables=tables,
        unconfirmed=sum(t.label is None and t.suggestion is not None for t in tables),
        drifted=sum(bool(t.drift) for t in tables),
    )


def _review(
    ref: TableRef,
    entry: CatalogTable | None,
    imports: dict[str, TableSchema],
    imported_environments: dict[str, list[str]],
    used_by: list[str],
    keys: list[KeyTable],
) -> TableReview:
    first = next(iter(imports.values()), None)
    drift = environment_drift(imports)
    if entry is not None:
        # Checked in every environment this connector was imported in, found there or not.
        for environment in imported_environments.get(ref[0], []):
            drift.extend(catalog_drift(entry, environment, imports.get(environment)))
    return TableReview(
        connector=ref[0],
        schema=first.db_schema if first else None,
        table=first.table if first else entry.table if entry else ref[1],
        label=entry.label if entry else None,
        suggestion=suggest(first, ref[0], keys) if first else None,
        imported_in=list(imports),
        drift=drift,
        used_by=used_by,
    )
