# Author: @SaadZarook
"""The Compare files routes. Files are processed in memory and never stored.

The server always masks. Reveal happens in the browser, from the file the person supplied: a masked
difference says where the value sits, and nothing else about it.
"""

from collections.abc import Callable, Coroutine
from decimal import Decimal
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, JsonValue

from plugins.compare_files.backend.parsers import Format, Parsed, detect, parse
from plugins.compare_files.backend.secrets import (
    Offsets,
    contains_shared,
    find_exactly,
    line_masker,
    looks_secret_line,
    masker,
    ranges_of,
    secrets_in,
    shared_secrets,
    without_shared,
)
from probeowl.plugins.api import (
    ArrayMatch,
    Change,
    Comparison,
    InputTooLarge,
    Leaf,
    LocalInput,
    Options,
    PluginContext,
    RequestError,
    Side,
    compare,
    compare_lines,
)

MAX_FILE_BYTES = 10_000_000
MAX_REQUEST_BYTES = 25_000_000
# Beyond what anyone can read, and each one costs memory in the browser: the summary stays exact.
MAX_DIFFERENCES = 10_000


class FormatChoice(StrEnum):
    """What the person asked for: detected from the content, or overridden."""

    AUTO = "auto"
    JSON = "json"
    YAML = "yaml"
    CSV = "csv"
    XML = "xml"
    PROPERTIES = "properties"
    INI = "ini"
    ENV = "env"
    TEXT = "text"


class SuppliedFile(BaseModel):
    """A file or text the person supplied."""

    name: str = Field(min_length=1, max_length=255)
    text: str


class CompareOptions(BaseModel):
    """What counts as the same."""

    ignore_paths: list[str] = Field(default_factory=list)
    ignore_key_order: bool = True
    arrays: ArrayMatch = ArrayMatch.BY_POSITION
    array_key: str | None = Field(
        default=None, description="With arrays by_key: the field, or the CSV column, to match on."
    )
    ignore_whitespace: bool = False
    ignore_case: bool = Field(default=False, description="Applies to values, not to keys.")
    number_tolerance: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)


class CompareRequest(BaseModel):
    """Two files to compare."""

    left: SuppliedFile
    right: SuppliedFile
    format: FormatChoice = FormatChoice.AUTO
    options: CompareOptions = Field(default_factory=CompareOptions)


class Range(BaseModel):
    """Where text sits in the supplied file, in the units a browser's strings count."""

    start: int
    end: int


class FileSummary(BaseModel):
    """What the audit also records. Never the content."""

    name: str
    size: int
    sha256: str
    format: Format


class FileDifference(BaseModel):
    """One path that differs. left and right are `[masked]` if masked."""

    path: str
    change: Change
    left: JsonValue = None
    right: JsonValue = None
    masked: bool = False
    reveal_left: Range | None = Field(
        default=None,
        description="Masked only: where the value sits in the left file. Null if it cannot be "
        "found exactly, so it has no Reveal.",
    )
    reveal_right: Range | None = None


class CompareSummary(BaseModel):
    """How much differs."""

    changed: int
    added: int
    removed: int
    unchanged: int
    ignored: int
    masked: int


class CompareResult(BaseModel):
    """What differs, masked. Everything here is safe to show and export."""

    left: FileSummary
    right: FileSummary
    summary: CompareSummary
    differences: list[FileDifference]
    notes: list[str]
    masked_left: list[Range] | None = Field(
        description="Everything to hide in the left file's side-by-side view. Null if a secret "
        "could not be located, so that view must not be shown."
    )
    masked_right: list[Range] | None = None


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)  # a Decimal, or a date from YAML


def _format(choice: FormatChoice, left: str, right: str, notes: list[str]) -> Format:
    if choice is not FormatChoice.AUTO:
        return Format(choice.value)
    found_left, found_right = detect(left), detect(right)
    if found_left is found_right:
        return found_left
    notes.append(
        f"the files look like different formats ({found_left.value} and {found_right.value}), so "
        "they were compared as plain text"
    )
    return Format.TEXT


def _secrets(file: LocalInput, parsed: Parsed) -> list[object]:
    if parsed.format is Format.TEXT:
        return [line for line in file.text.splitlines() if looks_secret_line(line)]
    try:
        return list(secrets_in(parsed.tree))
    except RecursionError:
        # Too deep to walk means its secrets cannot be vouched for anywhere, so nothing is shown.
        raise RequestError(f"{file.name} is nested too deeply to compare") from None


def _masked_ranges(file: LocalInput, own: list[object], shared: set[str]) -> list[Range] | None:
    found = ranges_of(file.text, own, shared)
    if found is None:
        return None
    units = Offsets(file.text)
    return [Range(start=units(start), end=units(end)) for start, end in found]


class _Recorder:
    """Remembers masked values by path, to say where they sit. They go no further than this."""

    def __init__(self, inner: Callable[[Leaf], bool]):
        self._inner = inner
        self.values: dict[tuple[Side, str], object] = {}

    def __call__(self, leaf: Leaf) -> bool:
        masked = self._inner(leaf)
        if masked:
            self.values[(leaf.side, leaf.path)] = leaf.value
        return masked


def _reveal(
    recorder: _Recorder, side: Side, path: str, file: LocalInput, units: Offsets
) -> Range | None:
    if (side, path) not in recorder.values:
        return None
    found = find_exactly(file.text, recorder.values[(side, path)])
    return Range(start=units(found[0]), end=units(found[1])) if found else None


def _shown(value: object, shared: set[str]) -> JsonValue:
    shown = _json_value(value)
    return without_shared(shown, shared) if isinstance(shown, str) else shown


def _differences(
    result: Comparison,
    recorder: _Recorder,
    left: LocalInput,
    right: LocalInput,
    shared: set[str],
) -> list[FileDifference]:
    left_units, right_units = Offsets(left.text), Offsets(right.text)
    return [
        FileDifference(
            # A key can hold a secret too, and it reaches the path without passing the masker.
            path=without_shared(d.path, shared),
            change=d.change,
            left=_shown(d.left, shared),
            right=_shown(d.right, shared),
            masked=d.masked,
            reveal_left=_reveal(recorder, Side.LEFT, d.path, left, left_units)
            if d.masked
            else None,
            reveal_right=_reveal(recorder, Side.RIGHT, d.path, right, right_units)
            if d.masked
            else None,
        )
        for d in result.differences[:MAX_DIFFERENCES]
    ]


def run(body: CompareRequest, context: PluginContext) -> CompareResult:
    """Compare two supplied files. Audits the names, sizes and hashes, and nothing else."""
    left = context.local_input(body.left.name, body.left.text, MAX_FILE_BYTES)
    right = context.local_input(body.right.name, body.right.text, MAX_FILE_BYTES)
    notes: list[str] = []
    format_ = _format(body.format, left.text, right.text, notes)
    parsed_left, parsed_right = (
        parse(left.name, left.text, format_),
        parse(right.name, right.text, format_),
    )
    secrets_left, secrets_right = _secrets(left, parsed_left), _secrets(right, parsed_right)
    # Found on either side, hidden on both: the same text under an ordinary key is the same secret.
    shared = shared_secrets([*secrets_left, *secrets_right])
    own_rule = line_masker if format_ is Format.TEXT else masker

    def masked_here(leaf: Leaf) -> bool:
        return own_rule(leaf) or contains_shared(leaf.value, shared)

    recorder = _Recorder(masked_here)
    options = Options(
        ignore_paths=tuple(body.options.ignore_paths),
        ignore_key_order=body.options.ignore_key_order,
        arrays=body.options.arrays,
        array_key=body.options.array_key,
        ignore_whitespace=body.options.ignore_whitespace,
        ignore_case=body.options.ignore_case,
        number_tolerance=body.options.number_tolerance,
        masker=recorder,
    )
    if format_ is Format.TEXT:
        result = compare_lines(left.text, right.text, options)
    else:
        result = compare(parsed_left.tree, parsed_right.tree, options)
    notes += result.notes
    if len(result.differences) > MAX_DIFFERENCES:
        notes.append(
            f"showing the first {MAX_DIFFERENCES:,} of {len(result.differences):,} differences"
        )
    masked_left = _masked_ranges(left, secrets_left, shared)
    masked_right = _masked_ranges(right, secrets_right, shared)
    if masked_left is None or masked_right is None:
        notes.append(
            "the side-by-side view is hidden: a masked value could not be located in the file"
        )
        masked_left = masked_right = None
    context.audit(
        "files_compared",
        {
            "format": format_.value,
            "left": {"name": left.name, "size": left.size, "sha256": left.sha256},
            "right": {"name": right.name, "size": right.size, "sha256": right.sha256},
        },
    )
    return CompareResult(
        left=FileSummary(name=left.name, size=left.size, sha256=left.sha256, format=format_),
        right=FileSummary(name=right.name, size=right.size, sha256=right.sha256, format=format_),
        summary=CompareSummary(
            changed=result.count(Change.CHANGED),
            added=result.count(Change.ADDED),
            removed=result.count(Change.REMOVED),
            unchanged=result.unchanged,
            ignored=result.ignored,
            masked=result.masked,
        ),
        differences=_differences(result, recorder, left, right, shared),
        notes=notes,
        masked_left=masked_left,
        masked_right=masked_right,
    )


class _CappedRoute(APIRoute):
    """Refuses a request past the limit while reading it, before anything is parsed."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def capped(request: Request) -> Response:
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
                raise _too_large(int(declared))
            # Counted as it arrives too: a chunked request declares no length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_REQUEST_BYTES:
                    raise _too_large(len(body))
            # What request.body() returns from here on; the stream can only be read once.
            request._body = bytes(body)
            return await original(request)

        return capped


def _too_large(size: int) -> InputTooLarge:
    return InputTooLarge(
        f"the request was too large: at least {size:,} bytes, and the limit is "
        f"{MAX_REQUEST_BYTES:,} bytes. Compare smaller files or excerpts."
    )


def register(context: PluginContext) -> APIRouter:
    """The plugin's routes."""
    router = APIRouter(route_class=_CappedRoute)

    @router.post("/compare", responses={413: {}, 422: {}})
    def compare_files(body: CompareRequest) -> CompareResult:
        """Compare two files a person uploaded or pasted. Nothing is stored.

        Masked values never come back: only where each sits in the file supplied. A secret found
        on either side is masked wherever its exact text appears in both, if 8 characters or more.
        413 past 10 MB per file or 25 MB per request; 422 if a file is not valid in the chosen
        format, never quoting its content.
        """
        return run(body, context)

    return router
