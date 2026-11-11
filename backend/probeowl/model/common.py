# Author: @SaadZarook
"""Shared field types and the base model for every ProbeOwl model."""

from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


class ProbeOwlModel(BaseModel):
    """Base model: unknown keys are an error, so a typo in YAML never passes silently."""

    model_config = ConfigDict(extra="forbid")

    # Fields that may be left out where the model is read, but are always set where it is
    # returned: the API contract marks them required, so clients never fill them in.
    always_returned: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        if handler.mode == "serialization" and cls.always_returned:
            target: dict[str, Any] = handler.resolve_ref_schema(schema)
            for name in cls.always_returned:
                target["properties"][name].pop("default", None)
                target.setdefault("required", []).append(name)
        return schema


def humanized(name: str) -> str:
    """A name as people read it, when it has no title: visitor_cards is Visitor cards."""
    spaced = name.replace("_", " ")
    return spaced[:1].upper() + spaced[1:]


TITLE_DESCRIPTION = (
    "How the web app and the run header name it. Omitted: its name with spaces for "
    "underscores, so visitor_cards is Visitor cards."
)


# No dots or hyphens: names appear in expressions such as `count(ledger)` and
# in field references such as `record.ledger_txn_id`.
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]

# Columns keep their database case (Oracle returns TXN_ID).
FieldName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)]

FieldPath = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$",
        description="A field, or a dotted path into nested fields: customer.email.",
    ),
]

FieldRef = Annotated[
    str,
    Field(
        pattern=r"^[a-z][a-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$",
        description="A field of a location, written <location>.<field>.",
    ),
]

Duration = Annotated[
    str,
    Field(pattern=r"^[1-9][0-9]*(ms|s|m|h|d)$", description="A duration such as 2s, 2m, 1h or 7d."),
]

_UNIT_SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


def seconds(duration: str) -> float:
    """A Duration in seconds: '90s' is 90.0, '2m' is 120.0."""
    unit = "ms" if duration.endswith("ms") else duration[-1]
    return int(duration[: -len(unit)]) * _UNIT_SECONDS[unit]


SecretRef = Annotated[
    str,
    Field(
        pattern=r"^(keyring:[^/\s]+/\S+|env:[A-Za-z_][A-Za-z0-9_]*|vault://\S+)$",
        description=(
            "Where a credential lives: keyring:<service>/<username>, env:<VARIABLE> "
            "or vault://<path>. Never the credential itself."
        ),
    ),
]


def split_field_ref(ref: str) -> tuple[str, str]:
    """Split `<location>.<field>` into its two parts."""
    location, field = ref.split(".", 1)
    return location, field
