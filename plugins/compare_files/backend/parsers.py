# Author: @SaadZarook
"""Read the eight formats into one shape: a tree of mappings, lists and scalars, or lines.

Everything here reads text a person supplied, which may not be what it claims to be. Errors never
quote the content, because a file may hold a secret: a line number at most.
"""

import configparser
import csv
import io
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any
from xml.etree.ElementTree import Element, ParseError

import yaml
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from probeowl.plugins.api import RequestError

# YAML aliases can expand a small file into billions of nodes. Past this it is refused.
MAX_YAML_NODES = 2_000_000
MAX_CSV_FIELD = 10_000_000
_NUMBER = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")
_ENV_LINE = re.compile(r"^(export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_INI_SECTION = re.compile(r"^\[[^\]\n]+\]\s*$", re.MULTILINE)


class Format(StrEnum):
    """What a file is."""

    JSON = "json"
    YAML = "yaml"
    CSV = "csv"
    XML = "xml"
    PROPERTIES = "properties"
    INI = "ini"
    ENV = "env"
    TEXT = "text"


@dataclass(frozen=True)
class Parsed:
    """A file read: a tree for a structured format, and None for plain text."""

    format: Format
    tree: object


def _refuse(name: str, what: str, detail: str = "") -> RequestError:
    suffix = f" ({detail})" if detail else ""
    return RequestError(f"{name} is not valid {what}{suffix}")


def _scalar(text: str) -> object:
    # So a number tolerance works on formats where everything is text. Leading zeros stay text:
    # 007 is an identifier, and 7 would hide a real difference.
    return Decimal(text) if _NUMBER.match(text) else text


def _json(name: str, text: str) -> object:
    try:
        return json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as err:
        raise _refuse(name, "JSON", f"line {err.lineno}") from None
    except (RecursionError, ValueError):
        raise _refuse(name, "JSON", "nested too deeply, or a number too long") from None


def _yaml_nodes_within_limit(node: yaml.Node | None, name: str) -> None:
    # Counts what walking the document would visit, aliases expanded, and stops at the limit.
    pending = [node] if node is not None else []
    seen = 0
    while pending:
        current = pending.pop()
        seen += 1
        if seen > MAX_YAML_NODES:
            raise _refuse(name, "YAML", "it expands to too many values")
        if isinstance(current, yaml.SequenceNode):
            pending.extend(current.value)
        elif isinstance(current, yaml.MappingNode):
            pending.extend(item for pair in current.value for item in pair)


def _yaml(name: str, text: str) -> object:
    try:
        for node in yaml.compose_all(text, Loader=yaml.SafeLoader):
            _yaml_nodes_within_limit(node, name)
        documents = list(yaml.safe_load_all(text))
    except yaml.MarkedYAMLError as err:
        mark = err.problem_mark
        raise _refuse(name, "YAML", f"line {mark.line + 1}" if mark else "") from None
    except (yaml.YAMLError, RecursionError, ValueError):
        raise _refuse(name, "YAML") from None
    return documents[0] if len(documents) == 1 else documents


def _csv(name: str, text: str) -> object:
    # The field limit is process-wide, and csv's default would refuse a long field in a legal file.
    csv.field_size_limit(MAX_CSV_FIELD)
    delimiter = (
        ";" if text.split("\n", 1)[0].count(";") > text.split("\n", 1)[0].count(",") else ","
    )
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        rows = [row for row in reader if row]
    except csv.Error:
        raise _refuse(name, "CSV") from None
    if not rows:
        return []
    header, *body = rows
    if len(set(header)) != len(header):
        raise _refuse(name, "CSV", "a column name is repeated")
    for number, row in enumerate(body, start=2):
        if len(row) != len(header):
            raise _refuse(name, "CSV", f"row {number} has {len(row)} of {len(header)} columns")
    return [{k: _scalar(v) for k, v in zip(header, row, strict=True)} for row in body]


def _element(element: Element) -> object:
    children: dict[str, object] = {f"@{k}": _scalar(v) for k, v in element.attrib.items()}
    for child in element:
        value = _element(child)
        existing = children.get(child.tag)
        if child.tag not in children:
            children[child.tag] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            children[child.tag] = [existing, value]
    text = (element.text or "").strip()
    if not children:
        return _scalar(text) if text else None
    if text:
        children["#text"] = _scalar(text)
    return children


def _xml(name: str, text: str) -> object:
    # defusedxml, because entity expansion and external entities are refused rather than obeyed.
    try:
        root = ElementTree.fromstring(text)
    except ParseError as err:
        raise _refuse(name, "XML", f"line {err.position[0]}") from None
    except DefusedXmlException:
        raise _refuse(name, "XML", "it uses entities or a DTD, which are refused") from None
    except RecursionError:
        raise _refuse(name, "XML", "nested too deeply") from None
    try:
        return {root.tag: _element(root)}
    except RecursionError:
        raise _refuse(name, "XML", "nested too deeply") from None


def _nest(flat: dict[str, object]) -> dict[str, Any]:
    # So a.b.c is the path it looks like. A key that is both a value and a parent stays flat.
    tree: dict[str, Any] = {}
    for key, value in flat.items():
        parts = key.split(".")
        node = tree
        for part in parts[:-1]:
            child = node.get(part)
            if child is None:
                child = node[part] = {}
            elif not isinstance(child, dict):
                tree[key] = value
                break
            node = child
        else:
            if isinstance(node.get(parts[-1]), dict):
                tree[key] = value
            else:
                node[parts[-1]] = value
    return tree


def _properties(text: str) -> object:
    flat: dict[str, object] = {}
    pending = ""
    for raw in text.splitlines():
        line = pending + raw.strip()
        pending = ""
        if line.endswith("\\") and not line.endswith("\\\\"):
            pending = line[:-1]
            continue
        if not line or line[0] in "#!":
            continue
        match = re.match(r"^((?:\\.|[^\s=:\\])+)\s*[=:\s]\s*(.*)$", line)
        key, value = (match.group(1), match.group(2)) if match else (line, "")
        flat[key.replace("\\", "")] = _scalar(value)
    return _nest(flat)


def _ini(name: str, text: str) -> object:
    # No interpolation: a value containing % is data, and resolving it would change what it says.
    parser = configparser.RawConfigParser(strict=False, interpolation=None, default_section="\0")
    parser.optionxform = str  # type: ignore[assignment, method-assign]
    try:
        parser.read_string(text)
    except configparser.MissingSectionHeaderError as err:
        # Its own case: it is a ParsingError with no errors list, and its text quotes the line.
        raise _refuse(name, "INI", f"line {err.lineno}") from None
    except configparser.ParsingError as err:
        line = err.errors[0][0] if err.errors else 0
        raise _refuse(name, "INI", f"line {line}") from None
    except configparser.Error:
        raise _refuse(name, "INI") from None
    return {
        section: {key: _scalar(value or "") for key, value in parser.items(section)}
        for section in parser.sections()
    }


def _env(name: str, text: str) -> object:
    values: dict[str, object] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if match is None:
            raise _refuse(name, ".env", f"line {number}")
        value = match.group(3).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[match.group(2)] = _scalar(value)
    return values


def _looks_like(text: str) -> Format:
    lines = [
        ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not lines:
        return Format.TEXT
    if all(
        _ENV_LINE.match(line) and line.split("=", 1)[0].replace("export ", "").isupper()
        for line in lines
    ):
        return Format.ENV
    if _INI_SECTION.search(text) and all(
        "=" in ln or ":" in ln or ln.startswith(("[", ";")) for ln in lines
    ):
        return Format.INI
    if all(re.match(r"^[\w.\-]+\s*=", line) for line in lines):
        return Format.PROPERTIES
    return Format.TEXT


def detect(text: str) -> Format:
    """What the content looks like. The person can override it."""
    start = text.lstrip()[:1]
    if start in {"{", "["}:
        try:
            json.loads(text)
            return Format.JSON
        except (ValueError, RecursionError):
            pass  # [section] or a YAML flow mapping: the checks below decide
    if start == "<":
        return Format.XML
    found = _looks_like(text)
    if found is not Format.TEXT:
        return found
    if _is_csv(text):
        return Format.CSV
    if _is_yaml_structure(text):
        return Format.YAML
    return Format.TEXT


def _is_csv(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    for delimiter in (",", ";"):
        try:
            widths = {
                len(r) for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter) if r
            }
        except csv.Error:
            continue
        if len(widths) == 1 and widths != {1}:
            return True
    return False


def _is_yaml_structure(text: str) -> bool:
    try:
        return isinstance(yaml.safe_load(text), dict | list)
    except (yaml.YAMLError, RecursionError, ValueError):
        return False


def parse(name: str, text: str, wanted: Format) -> Parsed:
    """The file read as wanted; RequestError naming the file, never quoting it, if it cannot be."""
    readers = {
        Format.JSON: lambda: _json(name, text),
        Format.YAML: lambda: _yaml(name, text),
        Format.CSV: lambda: _csv(name, text),
        Format.XML: lambda: _xml(name, text),
        Format.PROPERTIES: lambda: _properties(text),
        Format.INI: lambda: _ini(name, text),
        Format.ENV: lambda: _env(name, text),
        Format.TEXT: lambda: None,
    }
    return Parsed(wanted, readers[wanted]())
