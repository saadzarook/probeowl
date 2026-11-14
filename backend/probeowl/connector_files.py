# Author: @SaadZarook
"""A connector's YAML file: built new, or given more bindings with its comments and order kept.

Shared by owl init, owl connector add and the Add connection form, so a connector is written
the same way whichever made it. Nothing here writes a file or reads a secret.
"""

import io
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML

from probeowl.errors import ConfigError
from probeowl.model.check import LoadedApplication

KEYCHAIN_SERVICE = "probeowl"
DEFAULT_ROW_CEILING = 1000


def keychain_reference(connector: str, environment: str) -> str:
    """Where a binding's password is kept: keyring:probeowl/<connector>.<environment>."""
    return f"keyring:{KEYCHAIN_SERVICE}/{connector}.{environment}"


def new_connector_text(header: str, document: dict[str, Any]) -> str:
    """A new connector file: a header comment, then the document in the order given."""
    return header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def with_bindings(text: str, bindings: dict[str, dict[str, Any]]) -> str:
    """The connector's file with the new bindings added, its comments and order kept."""
    editor = _round_trip()
    document = editor.load(text)
    for env, binding in bindings.items():
        document["environments"][env] = binding
    out = io.StringIO()
    editor.dump(document, out)
    return out.getvalue()


def file_of(app: LoadedApplication, connector: str) -> Path:
    """The file that defines the connector; ConfigError if it shares the file with others."""
    if app.folder is None:
        raise ConfigError(f"{app.application.application} was not loaded from a folder")
    for path in sorted(app.folder.rglob("*.yaml")):
        documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        if any(isinstance(d, dict) and d.get("connector") == connector for d in documents):
            if len(documents) > 1:
                raise ConfigError(f"{path} holds more than {connector}; move it to its own file")
            return path
    raise ConfigError(f"no file in {app.folder} defines connector {connector}")


def _round_trip() -> YAML:
    # As the examples are written: two-space mappings, list items indented under their key.
    editor = YAML()
    editor.width = 100
    editor.indent(mapping=2, sequence=4, offset=2)
    return editor
