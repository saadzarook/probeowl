# Author: @SaadZarook
"""Entity, identifier and location models, with the key graph checks."""

import re
from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from probeowl.model.common import (
    TITLE_DESCRIPTION,
    Duration,
    FieldName,
    FieldPath,
    FieldRef,
    Name,
    ProbeOwlModel,
    humanized,
    split_field_ref,
)


class Cardinality(StrEnum):
    """How many records a location should return."""

    ONE = "one"
    OPTIONAL = "optional"
    MANY = "many"


class Identifier(ProbeOwlModel):
    """A value that finds a subject. An identifier that matches several subjects is Ambiguous."""

    pattern: str | None = Field(
        default=None, description="A regular expression used to detect this identifier's type."
    )
    via: FieldRef | None = Field(
        default=None,
        description=(
            "<location>.<field> to look this identifier up in. The location must expose "
            "the root key. Omitted only for the root key itself."
        ),
    )
    unique: bool
    pii: bool = False

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as err:
                raise ValueError(f"pattern is not a valid regular expression: {err}") from err
        return value


class LocationBase(ProbeOwlModel):
    """Fields shared by locations and fan-out locations."""

    always_returned = ("title",)

    title: str = Field(default="", description=TITLE_DESCRIPTION)
    using: Name = Field(description="The connector that does the lookup.")
    lookup: str = Field(
        min_length=1, description="A single SELECT with bound parameters, or GET <path>."
    )
    fallback: str | None = Field(default=None, description="Tried when the lookup finds nothing.")
    cardinality: Cardinality = Cardinality.ONE
    exposes: list[FieldName] = Field(
        default_factory=list, description="Fields other locations and identifiers may use."
    )
    pii: list[FieldName] = Field(
        default_factory=list,
        description="SQL locations: columns masked in every result and in evidence.",
    )
    mask: list[FieldPath] = Field(
        default_factory=list,
        description=(
            "REST locations: fields of the JSON response masked in every result and in "
            "evidence, as dotted paths such as customer.email."
        ),
    )
    within: Duration | None = Field(
        default=None, description="How long to report Waiting before Missing."
    )
    anchor_time: FieldRef | None = Field(
        default=None, description="The record's own timestamp the window starts from."
    )
    compare: list[FieldPath] = Field(
        default_factory=list,
        description=(
            "REST locations: fields of the JSON response a healthy comparison shows. "
            "SQL locations mark compare columns in the table catalog instead."
        ),
    )
    expected_to_vary: list[FieldPath] = Field(
        default_factory=list,
        description=(
            "REST locations: compare fields that normally differ between two healthy records. "
            "Shown in the differences, but never highlighted as a likely cause."
        ),
    )

    @model_validator(mode="after")
    def _anchor_needs_window(self) -> Self:
        if self.anchor_time is not None and self.within is None:
            raise ValueError("anchor_time needs a within window")
        return self

    @model_validator(mode="after")
    def _varying_fields_are_compared(self) -> Self:
        extra = sorted(set(self.expected_to_vary) - set(self.compare))
        if extra:
            raise ValueError(f"expected_to_vary lists fields not in compare: {', '.join(extra)}")
        return self


class FanOutLocation(LocationBase):
    """A lookup repeated for each row of a many location. Its lookup uses that row's fields."""

    max_fanout: int = Field(gt=0, description="The most rows this lookup is repeated for.")


class Location(LocationBase):
    """One place a subject's data lives, with its own key and lookup."""

    key_from: FieldRef | None = Field(
        default=None,
        description="<location>.<field> that gives this location its key. Omitted: the root key.",
    )
    max_rows: int | None = Field(
        default=None, gt=0, description="Row cap for a many location; results say if cut."
    )
    each: dict[Name, FanOutLocation] = Field(
        default_factory=dict, description="Lookups repeated per row of a many location."
    )

    @model_validator(mode="after")
    def _many_only_fields(self) -> Self:
        if self.cardinality is not Cardinality.MANY:
            if self.max_rows is not None:
                raise ValueError("max_rows applies only to cardinality many")
            if self.each:
                raise ValueError("each applies only to cardinality many")
        return self


class HealthyNarrowing(ProbeOwlModel):
    """A closer match a person can choose, as a lookup of its own: same payment method, say."""

    title: str = Field(min_length=1)
    lookup: str = Field(min_length=1, description="As healthy_sample's lookup, with more filters.")


class HealthySample(ProbeOwlModel):
    """How to find known-good subjects to compare a failing one with. SQL connectors only."""

    using: Name = Field(description="The SQL connector that does the lookup.")
    lookup: str = Field(
        min_length=1,
        description=(
            "A single SELECT of the root key, newest first, leaving out the failing subject, "
            "on an indexed column. Its rows are candidates: each is proved by running the probe."
        ),
    )
    parameters: dict[FieldName, FieldRef] = Field(
        default_factory=dict,
        description=(
            "Where each :parameter comes from in the failing run: <location>.<field>, "
            "from a location with one row, and never a masked field."
        ),
    )
    narrow: dict[Name, HealthyNarrowing] = Field(
        default_factory=dict, description="Closer matches a person can choose."
    )


class Entity(ProbeOwlModel):
    """A business subject type, its identifiers and the locations its data lives in."""

    always_returned = ("title",)

    entity: Name
    title: str = Field(default="", description=TITLE_DESCRIPTION)
    root_key: FieldName = Field(description="The identifier every other lookup starts from.")
    owner: str | None = None
    identifiers: dict[FieldName, Identifier] = Field(
        default_factory=dict,
        description="Every identifier the entity can be found by. The root key is added if absent.",
    )
    locations: dict[Name, Location] = Field(min_length=1)
    healthy_sample: HealthySample | None = Field(
        default=None,
        description="How to find a known-good subject. Omitted: a known-good ID is pasted.",
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        self._add_titles()
        self._add_root_identifier()
        self._check_fanout_names()
        self._check_identifiers()
        self._check_key_graph()
        self._check_healthy_sample()
        return self

    def _add_titles(self) -> None:
        # Set once here, so every reader shows the same name and none makes up its own.
        self.title = self.title or humanized(self.entity)
        for name, location in self.locations.items():
            location.title = location.title or humanized(name)
            for child, fanned in location.each.items():
                fanned.title = fanned.title or humanized(child)

    def _add_root_identifier(self) -> None:
        if self.root_key not in self.identifiers:
            self.identifiers[self.root_key] = Identifier(unique=True)

    def _check_fanout_names(self) -> None:
        # Expressions name fan-out locations directly (`controller.status`).
        for name, location in self.locations.items():
            for child in location.each:
                if child in self.locations:
                    raise ValueError(f"fan-out location {name}.{child} reuses a location name")

    def _check_identifiers(self) -> None:
        for name, identifier in self.identifiers.items():
            if name == self.root_key:
                if identifier.via is not None:
                    raise ValueError(f"root key {name} cannot have via")
                if not identifier.unique:
                    raise ValueError(f"root key {name} must be unique")
                continue
            if identifier.via is None:
                raise ValueError(f"identifier {name} needs via: <location>.<field>")
            location_name, field = split_field_ref(identifier.via)
            exposes = self._exposes(location_name, f"identifier {name} via")
            for needed in (field, self.root_key):
                if needed not in exposes:
                    raise ValueError(
                        f"identifier {name} via {identifier.via}: "
                        f"location {location_name} must expose {needed}"
                    )

    def _check_key_graph(self) -> None:
        for name, location in self.locations.items():
            for label, ref in (
                ("key_from", location.key_from),
                ("anchor_time", location.anchor_time),
            ):
                if ref is None:
                    continue
                source, field = split_field_ref(ref)
                if field not in self._exposes(source, f"{name}.{label}"):
                    raise ValueError(f"{name}.{label}: location {source} does not expose {field}")
        for name in self.locations:
            self._check_no_cycle(name)

    def _check_healthy_sample(self) -> None:
        sample = self.healthy_sample
        if sample is None:
            return
        for parameter, ref in sample.parameters.items():
            used_by = f"healthy_sample parameter {parameter}"
            source, field = split_field_ref(ref)
            if field not in self._exposes(source, used_by):
                raise ValueError(f"{used_by}: location {source} does not expose {field}")
            location = self.locations[source]
            if location.cardinality is Cardinality.MANY:
                raise ValueError(f"{used_by}: {source} can have many rows, so {ref} has no value")
            # A run keeps only the masked value, so there is nothing true to bind.
            if field.lower() in {name.lower() for name in [*location.pii, *location.mask]}:
                raise ValueError(f"{used_by}: {ref} is masked, so it cannot be a parameter")

    def _check_no_cycle(self, start: str) -> None:
        seen = [start]
        current = self.locations[start].key_from
        while current is not None:
            source, _ = split_field_ref(current)
            if source in seen:
                raise ValueError(f"key_from cycle: {' -> '.join([*seen, source])}")
            seen.append(source)
            current = self.locations[source].key_from

    def _exposes(self, location_name: str, used_by: str) -> list[str]:
        location = self.locations.get(location_name)
        if location is None:
            raise ValueError(f"{used_by}: no location named {location_name}")
        return location.exposes
