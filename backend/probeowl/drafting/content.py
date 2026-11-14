# Author: @SaadZarook
"""What owl mcp's drafting tools hand an assistant: guides, templates and examples (#39).

Placeholders only. A template names no host, port, database, login or URL, and its secret is
`keyring:probeowl/<connector>.<env>`. An example connector has every one of those replaced by
its placeholder before it leaves here.
"""

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from probeowl.model.catalog import Catalog
from probeowl.model.connector import SqlConnector
from probeowl.model.entity import Entity
from probeowl.model.probe import Probe
from probeowl.model.runbook import Runbook


class DraftKind(StrEnum):
    """What a draft is: one file in a config folder."""

    ENTITY = "entity"
    PROBE = "probe"
    CONNECTOR = "connector"
    CATALOG = "catalog"
    RUNBOOK = "runbook"


MODELS: dict[DraftKind, type[BaseModel]] = {
    DraftKind.ENTITY: Entity,
    DraftKind.PROBE: Probe,
    DraftKind.CONNECTOR: SqlConnector,
    DraftKind.CATALOG: Catalog,
    DraftKind.RUNBOOK: Runbook,
}

FILES = {
    DraftKind.ENTITY: "<application>/entities/<entity>.yaml",
    DraftKind.PROBE: "<application>/probes/<probe>.yaml",
    DraftKind.CONNECTOR: "<application>/connectors/<connector>.yaml",
    DraftKind.CATALOG: "<application>/catalog.yaml",
    DraftKind.RUNBOOK: "<application>/runbooks/<runbook>.yaml",
}

GUIDE_PAGES = {
    DraftKind.ENTITY: "/guide/first-application/",
    DraftKind.PROBE: "/guide/writing-probes/",
    DraftKind.CONNECTOR: "/guide/connectors/",
    DraftKind.CATALOG: "/guide/first-application/",
    DraftKind.RUNBOOK: "/guide/writing-runbooks/",
}

RULES: dict[DraftKind, list[str]] = {
    DraftKind.ENTITY: [
        "Every lookup is a single SELECT, with :parameters for every value: never a literal.",
        "A Transaction, Audit or Log table is filtered by its time column, or looked up by a key.",
        "root_key is the identifier every lookup starts from; others name their via location.",
        "cardinality is one, optional or many; a many location is capped by max_rows.",
        "Mark PII columns in pii, and PII identifiers with pii: true: they are always masked.",
        "key_from names where a location gets its key: <location>.<field> it exposes.",
    ],
    DraftKind.PROBE: [
        "kind is inspect (current state) or validate (a flow executed correctly).",
        "Each expectation is one rule over the entity's locations, such as count(ledger) == 2.",
        "The probe's entity must exist in the same application.",
    ],
    DraftKind.CONNECTOR: [
        "Read only: bind a read replica, readable secondary or Active Data Guard standby.",
        "secret is keyring:probeowl/<connector>.<env> or env:<VARIABLE>, never a password.",
        "Write the host, database and login in the file yourself: an assistant never sees them.",
    ],
    DraftKind.CATALOG: [
        "Each table has one label: transaction, master, audit, log, reference, "
        "external_mapping or outbox.",
        "Mark column roles: primary_key, time, status, link, external_key, pii, compare.",
    ],
    DraftKind.RUNBOOK: [
        "A read query step is one SELECT, reviewed with its sql_sha256; write queries never run.",
        "applies_to names the entity and the location outcomes that suggest it.",
    ],
}

MISTAKES: dict[DraftKind, list[str]] = {
    DraftKind.ENTITY: [
        "A value written into the SQL: pass it as a :parameter.",
        "A Transaction table read without its time column: add created_at >= :since, or a key.",
        "A PII column left unmarked: list it in the location's pii.",
    ],
    DraftKind.PROBE: [
        "An expectation naming a location the entity doesn't have.",
        "A probe for an entity in another application.",
    ],
    DraftKind.CONNECTOR: [
        "A password in the file: use keyring:probeowl/<connector>.<env> and save it with "
        "Add connection or owl connector add.",
        "A Production binding that isn't a replica: set replica: true only for a real one.",
    ],
    DraftKind.CATALOG: ["A transaction table with no time column marked."],
    DraftKind.RUNBOOK: ["A read query whose sql_sha256 no longer matches its SQL."],
}


def required_fields(kind: DraftKind) -> list[dict[str, str]]:
    """The fields the kind's schema requires, each with its meaning, from the model itself."""
    schema = MODELS[kind].model_json_schema()
    properties: dict[str, Any] = schema.get("properties", {})
    return [
        {"field": name, "meaning": str(properties.get(name, {}).get("description", ""))}
        for name in schema.get("required", [])
    ]


TEMPLATES: dict[DraftKind, str] = {
    DraftKind.ENTITY: """entity: <entity>
title: <Entity title>
root_key: <root_key>
owner: <owning team>

identifiers:
  <root_key>: { pattern: '<regular expression>', unique: true }

locations:
  <location>:
    title: <Location title>
    using: <connector>
    lookup: >
      SELECT <columns> FROM <table> WHERE <root_key> = :<root_key>
    exposes: [<root_key>, <column>]
    cardinality: one
""",
    DraftKind.PROBE: """probe: <probe>
title: <Probe title>
kind: validate
entity: <entity>
owner: <owning team>
steps:
  - expect: count(<location>) == 1
""",
    DraftKind.CONNECTOR: """connector: <connector>
type: <sqlserver | oracle | mariadb>
owner: <owning team>
environments:
  <env>:
    host: <host>
    port: <port>
    database: <database>
    user: <read-only login>
    secret: keyring:probeowl/<connector>.<env>
    replica: true   # only for a real read replica, readable secondary or standby
""",
    DraftKind.CATALOG: """catalog: <application>
tables:
  - connector: <connector>
    table: <table>
    label: <transaction | master | audit | log | reference | external_mapping | outbox>
    owner: <owning team>
    columns:
      <column>: { roles: [primary_key] }
""",
    DraftKind.RUNBOOK: """runbook: <runbook>
title: <Runbook title>
application: <application>
applies_to: { entity: <entity>, when: [<location>.missing] }
owner: <owning team>
last_verified: <YYYY-MM-DD>
steps:
  - kind: manual
    id: <step-id>
    title: <What to check by hand>
""",
}

# What an example connector's binding is reduced to: the template's placeholders.
_SAFE_BINDING = {
    "host": "<host>",
    "port": "<port>",
    "database": "<database>",
    "service_name": "<service name>",
    "user": "<read-only login>",
    "base_url": "<base URL>",
}


def examples_folder() -> Path:
    """The examples: in the wheel as package data, or in a checkout next to backend/."""
    bundled = Path(__file__).resolve().parent.parent / "bundled_examples"
    if bundled.is_dir():
        return bundled
    return Path(__file__).resolve().parents[3] / "examples"


def example_files(kind: DraftKind) -> list[Path]:
    """Each example file of the kind, in a stable order."""
    root = examples_folder()
    folder = {DraftKind.ENTITY: "entities", DraftKind.CATALOG: ""}.get(kind, f"{kind.value}s")
    if kind is DraftKind.CATALOG:
        return sorted(root.glob("*/catalog.yaml"))
    return sorted(root.glob(f"*/{folder}/*.yaml"))


def safe_example(kind: DraftKind, path: Path) -> str:
    """The example's YAML; a connector's with every host, URL, login and secret a placeholder."""
    text = path.read_text(encoding="utf-8")
    if kind is not DraftKind.CONNECTOR:
        return text
    data = yaml.safe_load(text)
    for binding in (data.get("environments") or {}).values():
        if not isinstance(binding, dict):
            continue
        for key, placeholder in _SAFE_BINDING.items():
            if key in binding:
                binding[key] = placeholder
        if "secret" in binding:
            # The form, never a keychain name: no tool returns one.
            binding["secret"] = "keyring:probeowl/<connector>.<env>"
    return yaml.safe_dump(data, sort_keys=False)
