# Author: @SaadZarook
"""`owl catalog`: import a connector's schema through owl serve."""

from typing import Annotated

import typer

from probeowl.cli import server
from probeowl.model.review import CatalogReview, TableReview
from probeowl.model.schema import SchemaSnapshot, TableSchema

catalog_app = typer.Typer(help="The table catalog: import schemas from the databases.")


@catalog_app.command("import")
def import_schema(
    connector: Annotated[str, typer.Argument(help="A SQL connector, such as wallet_db.")],
    application: Annotated[str, typer.Option(help="The application the connector belongs to.")],
    env: Annotated[str, typer.Option("--env", help="The environment, such as prod or uat.")],
    server_url: Annotated[str | None, typer.Option("--server", help=server.SERVER_HELP)] = None,
    config: server.NoConfig = None,
) -> None:
    """Import tables, columns, keys, indexes and row estimates; never data."""
    with server.connected(server_url) as api:
        response = api.post(
            f"/api/applications/{application}/catalog/imports",
            json={"connector": connector, "environment": env},
            timeout=120,
        )
        if response.status_code != 201:
            server.problem(response)
        review = CatalogReview.model_validate(
            server.get(api, f"/api/applications/{application}/catalog/review")
        )
    snapshot = SchemaSnapshot.model_validate(response.json())
    suggestions = {
        t.table.lower(): t for t in review.tables if t.connector == connector and t.suggestion
    }
    typer.echo(f"Imported {connector} in {env}: {len(snapshot.tables)} tables")
    for table in snapshot.tables:
        typer.echo(f"  {_describe(table)}")
        found = suggestions.get(table.table.lower())
        if found is not None and found.suggestion is not None:
            typer.echo(f"    {_suggested(found)}")
            if found.label is None:
                for column, target in found.suggestion.links.items():
                    typer.echo(f"    suggested link: {column} to {target.table}.{target.column}")
    if snapshot.truncated:
        typer.echo(f"warning: {snapshot.truncated}")


def _suggested(table: TableReview) -> str:
    assert table.suggestion is not None  # only tables with a suggestion are printed
    label = table.suggestion.label.value.replace("_", " ")
    reasons = "; ".join(table.suggestion.reasons)
    if table.label is None:
        return f"suggested: {label} ({reasons}); confirm it on the Tables screen"
    if table.label is table.suggestion.label:
        return f"confirmed: {label}"
    return f"confirmed: {table.label.value.replace('_', ' ')} (suggested: {label}, {reasons})"


def _describe(table: TableSchema) -> str:
    name = f"{table.db_schema}.{table.table}" if table.db_schema else table.table
    key = ", ".join(table.primary_key) or "none"
    rows = "rows unknown" if table.approx_rows is None else f"~{table.approx_rows} rows"
    return (
        f"{name:<32} {len(table.columns)} columns, primary key {key}, "
        f"{len(table.indexes)} indexes, {rows}"
    )
