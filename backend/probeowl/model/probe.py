# Author: @SaadZarook
"""Probe, step, expectation and condition models."""

from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BeforeValidator, ConfigDict, Field, model_validator

from probeowl.model.common import TITLE_DESCRIPTION, Name, ProbeOwlModel, humanized

Expectation = Annotated[
    str, Field(min_length=1, description="One rule, such as count(ledger) == 2.")
]
Condition = Annotated[
    str,
    Field(min_length=1, description="When the step applies, such as record.status == 'failed'."),
]


class ProbeKind(StrEnum):
    """Inspect checks current state; validate checks one flow. Health is phase 2."""

    INSPECT = "inspect"
    VALIDATE = "validate"
    HEALTH = "health"


def _as_list(value: Any) -> Any:
    # `expect: count(webhook) >= 1` is shorthand for a list of one.
    return [value] if isinstance(value, str) else value


ExpectationList = Annotated[
    list[Expectation],
    Field(min_length=1),
    BeforeValidator(
        _as_list,
        json_schema_input_type=Expectation | Annotated[list[Expectation], Field(min_length=1)],
    ),
]


class Step(ProbeOwlModel):
    """Expectations that apply together, only when the condition holds if one is given."""

    when: Condition | None = None
    expect: ExpectationList


def _add_shorthand(schema: dict[str, Any]) -> None:
    # The model only has `steps`; YAML may use a top-level `expect` instead.
    schema["properties"]["expect"] = {
        "anyOf": [
            {"type": "string", "minLength": 1},
            {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        ],
        "description": "Shorthand for steps: [{expect: ...}].",
    }
    schema["required"].remove("steps")
    schema["oneOf"] = [{"required": ["steps"]}, {"required": ["expect"]}]


class Probe(ProbeOwlModel):
    """A saved, reviewed check over an entity's locations. One model for inspect and validate."""

    model_config = ConfigDict(extra="forbid", json_schema_extra=_add_shorthand)
    always_returned = ("title",)

    probe: Name
    title: str = Field(default="", description=TITLE_DESCRIPTION)
    kind: ProbeKind
    entity: Name
    owner: str = Field(min_length=1, description="The team responsible for this probe.")
    description: str | None = None
    environments: list[Name] | None = Field(
        default=None,
        description=(
            "The environments this probe may run in. Omitted: every environment of its "
            "application. Every location needs a binding in each one."
        ),
    )
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalise_shorthand(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "expect" not in data:
            return data
        if "steps" in data:
            raise ValueError("use either steps or a top-level expect, not both")
        data = dict(data)
        data["steps"] = [{"expect": data.pop("expect")}]
        return data

    @model_validator(mode="after")
    def _phase_one_kinds(self) -> Self:
        if self.kind is ProbeKind.HEALTH:
            raise ValueError("health probes are not supported yet")
        return self

    @model_validator(mode="after")
    def _add_title(self) -> Self:
        self.title = self.title or humanized(self.probe)
        return self
