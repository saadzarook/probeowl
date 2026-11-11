# Author: @SaadZarook
"""Load ProbeOwl YAML files into models. The top-level key says what a document is."""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from probeowl.errors import ConfigError
from probeowl.model.application import Application
from probeowl.model.catalog import Catalog
from probeowl.model.connector import Connector, RestConnector, SqlConnector
from probeowl.model.entity import Entity
from probeowl.model.probe import Probe
from probeowl.model.runbook import Runbook

Document = Application | Catalog | SqlConnector | RestConnector | Entity | Probe | Runbook

# Checked in order; the first top-level key that matches decides the document's kind.
# Probe comes before entity because a probe also has an `entity:` key, and runbook
# before application because a runbook also has an `application:` key.
DOCUMENT_KINDS: dict[str, TypeAdapter[Any]] = {
    "probe": TypeAdapter(Probe),
    "entity": TypeAdapter(Entity),
    "connector": TypeAdapter(Connector),
    "runbook": TypeAdapter(Runbook),
    "application": TypeAdapter(Application),
    "catalog": TypeAdapter(Catalog),
}


def document_kind(data: object, source: str) -> str:
    """Return the kind of a parsed YAML document, from its top-level key."""
    if isinstance(data, dict):
        for kind in DOCUMENT_KINDS:
            if kind in data:
                return kind
    raise ConfigError(
        f"{source}: expected one of these top-level keys: {', '.join(DOCUMENT_KINDS)}"
    )


def parse_document(data: object, source: str) -> Document:
    """Validate one parsed YAML document."""
    kind = document_kind(data, source)
    try:
        document: Document = DOCUMENT_KINDS[kind].validate_python(data)
    except ValidationError as err:
        folded = _folded_checks(data) if kind == "probe" else None
        if folded is not None:
            raise ConfigError(f"{source}: {folded}") from err
        problems = "; ".join(
            f"{'.'.join(str(part) for part in e['loc']) or kind}: {e['msg']}" for e in err.errors()
        )
        raise ConfigError(f"{source}: invalid {kind}: {problems}") from err
    return document


# Text that reads as a check: a function of a location, or a comparison. A plain YAML value folds
# the next indented lines into itself, so a deleted `expect:` line leaves its checks in `owner`.
_CHECK_TEXT = re.compile(r"(count|exists|sum|min|max|all|any|none|unique)\(|[=!<>]=|\s[<>]\s")


def _folded_checks(data: object) -> str | None:
    """Why a probe has no checks, when its checks were folded into another field; else None."""
    if not isinstance(data, dict) or "expect" in data or "steps" in data:
        return None
    for field in ("owner", "title", "description", "entity", "kind", "probe"):
        value = data.get(field)
        if isinstance(value, str) and _CHECK_TEXT.search(value):
            shown = value if len(value) <= 60 else f"{value[:57]}..."
            return (
                f"this probe has no checks (expect:), and {field} looks like it holds check text "
                f"({shown!r}): is a line missing, such as expect: before the checks?"
            )
    return None


class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps dates as strings, as YAML 1.2 and the JSON Schemas do."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def read_text(text: str) -> Any:
    """Parse one YAML document from text, exactly as a file's are; yaml.YAMLError if it cannot."""
    return yaml.load(text, Loader=_Loader)


def read_yaml(path: Path) -> list[Any]:
    """Parse every document in a YAML file."""
    try:
        with path.open(encoding="utf-8") as file:
            return [doc for doc in yaml.load_all(file, Loader=_Loader) if doc is not None]
    except yaml.YAMLError as err:
        raise ConfigError(f"{path}: not valid YAML: {err}") from err


def load_file(path: Path) -> list[Document]:
    """Load and validate every document in one YAML file."""
    documents = read_yaml(path)
    if not documents:
        raise ConfigError(f"{path}: the file is empty")
    return [parse_document(doc, str(path)) for doc in documents]
