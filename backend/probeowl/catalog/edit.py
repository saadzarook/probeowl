# Author: @SaadZarook
"""Change one table's entry in catalog.yaml, leaving every other line of the file as it was.

The file is parsed only to find where the entry is; its lines are then replaced in the text,
so comments, spacing and layout elsewhere are untouched and the diff a person reviews before
committing shows only the table they confirmed.
"""

import io
from typing import Any

import yaml
from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from probeowl.errors import RequestError
from probeowl.model.catalog import Catalog, CatalogTable

# The order of keys in an entry, as in the examples.
_ENTRY_ORDER = (
    "connector",
    "table",
    "label",
    "owner",
    "description",
    "retention",
    "approx_rows",
    "columns",
)


def with_table(current: str | None, application: str, entry: CatalogTable) -> str:
    """catalog.yaml's text with entry added, or replacing the table it names."""
    if current is None:
        document = CommentedMap(catalog=application, tables=CommentedSeq([_entry(entry)]))
        header = f"# Tables the {application} probes read. Labels are confirmed in ProbeOwl.\n"
        return header + _dump(document)
    tables = _tables(current)
    lines = current.splitlines(keepends=True)
    if not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    new = _dump(CommentedSeq([_entry(entry)])).splitlines(keepends=True)
    found = next((i for i, t in enumerate(tables) if _names(t, entry)), None)
    if found is not None:
        start, end = _span(tables, found, lines)
        return "".join(lines[:start] + new + lines[end:])
    if not tables:
        raise RequestError("catalog.yaml has no tables list to add to")
    start, end = _span(tables, len(tables) - 1, lines)
    # Keep the file's spacing: a blank line between entries if it has them.
    gap = ["\n"] if start > 0 and not lines[start - 1].strip() else []
    return "".join(lines[:end] + gap + new + lines[end:])


def checked(text: str, application: str, connectors: set[str]) -> Catalog:
    """The catalog the text holds, checked as owl check checks it; RequestError if not."""
    try:
        catalog = Catalog.model_validate(yaml.safe_load(text))
    except ValidationError as err:
        raise RequestError(f"the catalog would not be valid: {err.errors()[0]['msg']}") from err
    if catalog.catalog != application:
        raise RequestError(f"catalog {catalog.catalog} is in application {application}")
    unknown = sorted({t.connector for t in catalog.tables} - connectors)
    if unknown:
        raise RequestError(f"{application} has no connector {', '.join(unknown)}")
    return catalog


def _tables(text: str) -> CommentedSeq:
    documents = list(_yaml().load_all(text))
    if len(documents) != 1 or not isinstance(documents[0], CommentedMap):
        raise RequestError(
            "catalog.yaml holds more than the catalog; move the catalog to its own file"
        )
    tables = documents[0].get("tables")
    if not isinstance(tables, CommentedSeq) or tables.fa.flow_style():
        raise RequestError("catalog.yaml's tables must be a list of entries, one per table")
    return tables


def _names(item: Any, entry: CatalogTable) -> bool:
    return (
        isinstance(item, CommentedMap)
        and item.get("connector") == entry.connector
        and str(item.get("table", "")).lower() == entry.table.lower()
    )


def _span(tables: CommentedSeq, index: int, lines: list[str]) -> tuple[int, int]:
    """The lines of entry index: from its first line to the next entry, or the end of the
    list, less the blank and comment lines at its end, which belong to what follows."""
    start = tables.lc.item(index)[0]
    if index + 1 < len(tables):
        end = tables.lc.item(index + 1)[0]
    else:
        indent = len(lines[start]) - len(lines[start].lstrip())
        end = next(
            (
                i
                for i in range(start + 1, len(lines))
                if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) < indent
            ),
            len(lines),
        )
    while end > start + 1 and (
        not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")
    ):
        end -= 1
    return start, end


def _entry(entry: CatalogTable) -> CommentedMap:
    data = entry.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    data.update(connector=entry.connector, table=entry.table, label=entry.label.value)
    ordered = CommentedMap((key, data[key]) for key in _ENTRY_ORDER if key in data)
    columns = CommentedMap()
    for name, column in entry.columns.items():
        # One line per column, as in the examples: id: {roles: [primary_key]}
        flow = CommentedMap(
            column.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        )
        flow.fa.set_flow_style()
        columns[name] = flow
    ordered["columns"] = columns
    return ordered


def _dump(document: Any) -> str:
    out = io.StringIO()
    _yaml().dump(document, out)
    return out.getvalue()


def _yaml() -> YAML:
    round_trip = YAML()
    round_trip.width = 100
    # As the examples are written: two-space mappings, list items indented under their key.
    round_trip.indent(mapping=2, sequence=4, offset=2)
    return round_trip
