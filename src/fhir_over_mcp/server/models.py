"""What the tools return.

Every item carries `ref`, the reference of the resource it came from, and its values are copied
from that resource field for field: codes keep their system and display, quantities keep their
unit, dates stay as recorded (FHIR allows partial dates). Nothing is parsed, converted or derived
(invariant 4). A field the resource doesn't have is `None`, never filled in.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Coding(BaseModel):
    system: str | None = None
    code: str | None = None
    display: str | None = None


class Concept(BaseModel):
    """A FHIR CodeableConcept: its codings and its text, as recorded."""

    codings: list[Coding] = Field(default_factory=list)
    text: str | None = None


class Quantity(BaseModel):
    value: float | int | None = None
    unit: str | None = None
    system: str | None = None
    code: str | None = None
    comparator: str | None = None


class HumanName(BaseModel):
    use: str | None = None
    family: str | None = None
    given: list[str] = Field(default_factory=list)
    prefix: list[str] = Field(default_factory=list)
    suffix: list[str] = Field(default_factory=list)
    text: str | None = None


class PatientItem(BaseModel):
    ref: str = Field(description="The Patient resource this came from, e.g. Patient/123.")
    names: list[HumanName]
    gender: str | None = None
    birth_date: str | None = Field(
        default=None, description="As recorded (YYYY, YYYY-MM or YYYY-MM-DD)."
    )


class ConditionItem(BaseModel):
    ref: str = Field(description="The Condition resource this came from.")
    code: Concept
    clinical_status: Concept | None = None
    verification_status: Concept | None = None
    onset: str | None = Field(default=None, description="onsetDateTime as recorded.")
    abatement: str | None = Field(default=None, description="abatementDateTime as recorded.")
    recorded_date: str | None = None


class DosageItem(BaseModel):
    """One dosage instruction, copied: its text if it has one, and its structured parts."""

    text: str | None = None
    timing_repeat: dict[str, Any] | None = Field(
        default=None, description="Timing.repeat exactly as recorded (frequency, period, ...)."
    )
    dose_quantity: Quantity | None = None
    as_needed: bool | None = None


class MedicationItem(BaseModel):
    ref: str = Field(description="The MedicationRequest resource this came from.")
    medication: Concept
    medication_ref: str | None = Field(
        default=None,
        description="When the request points at a Medication resource, its reference; "
        "`medication` was copied from that resource.",
    )
    status: str | None = None
    intent: str | None = None
    authored_on: str | None = None
    dosage: list[DosageItem] = Field(
        default_factory=list, description="Empty when the request records no dosage."
    )


class Component(BaseModel):
    code: Concept
    value_quantity: Quantity | None = None
    value_concept: Concept | None = None
    value_string: str | None = None


class ObservationItem(BaseModel):
    ref: str = Field(description="The Observation resource this came from.")
    code: Concept
    category: list[Concept] = Field(default_factory=list)
    status: str | None = None
    effective: str | None = Field(default=None, description="effectiveDateTime as recorded.")
    value_quantity: Quantity | None = None
    value_concept: Concept | None = None
    value_string: str | None = None
    components: list[Component] = Field(
        default_factory=list, description="For multi-part readings such as blood pressure."
    )


class _ListResult(BaseModel):
    matched: int = Field(description="How many items are in this result.")
    truncated: bool = Field(
        description="True if more matched than were returned; narrow the search to see them."
    )
    note: str = Field(
        description="A plain statement of what came back, including when nothing did."
    )


class PatientList(_ListResult):
    items: list[PatientItem]


class ConditionList(_ListResult):
    items: list[ConditionItem]


class MedicationList(_ListResult):
    items: list[MedicationItem]


class ObservationList(_ListResult):
    items: list[ObservationItem]
