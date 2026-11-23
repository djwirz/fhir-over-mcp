"""FHIR resource -> tool item. Pure functions: copy, never compute (invariant 4)."""

from __future__ import annotations

from typing import Any

from fhir_over_mcp.server.models import (
    Coding,
    Component,
    Concept,
    ConditionItem,
    DosageItem,
    HumanName,
    MedicationItem,
    ObservationItem,
    PatientItem,
    Quantity,
)

Resource = dict[str, Any]


def ref_of(resource: Resource) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def concept(raw: Resource | None) -> Concept | None:
    if raw is None:
        return None
    codings = [
        Coding(system=c.get("system"), code=c.get("code"), display=c.get("display"))
        for c in raw.get("coding", [])
    ]
    return Concept(codings=codings, text=raw.get("text"))


def _concept_required(raw: Resource | None) -> Concept:
    return concept(raw) or Concept()


def quantity(raw: Resource | None) -> Quantity | None:
    if raw is None:
        return None
    return Quantity(
        value=raw.get("value"),
        unit=raw.get("unit"),
        system=raw.get("system"),
        code=raw.get("code"),
        comparator=raw.get("comparator"),
    )


def patient_item(r: Resource) -> PatientItem:
    names = [
        HumanName(
            use=n.get("use"),
            family=n.get("family"),
            given=list(n.get("given", [])),
            prefix=list(n.get("prefix", [])),
            suffix=list(n.get("suffix", [])),
            text=n.get("text"),
        )
        for n in r.get("name", [])
    ]
    return PatientItem(
        ref=ref_of(r), names=names, gender=r.get("gender"), birth_date=r.get("birthDate")
    )


def condition_item(r: Resource) -> ConditionItem:
    return ConditionItem(
        ref=ref_of(r),
        code=_concept_required(r.get("code")),
        clinical_status=concept(r.get("clinicalStatus")),
        verification_status=concept(r.get("verificationStatus")),
        onset=r.get("onsetDateTime"),
        abatement=r.get("abatementDateTime"),
        recorded_date=r.get("recordedDate"),
    )


def _dosage(raw: Resource) -> DosageItem:
    dose = None
    for dr in raw.get("doseAndRate", []):
        if "doseQuantity" in dr:
            dose = quantity(dr["doseQuantity"])
            break
    return DosageItem(
        text=raw.get("text"),
        timing_repeat=raw.get("timing", {}).get("repeat"),
        dose_quantity=dose,
        as_needed=raw.get("asNeededBoolean"),
    )


def medication_item(r: Resource, medication: Resource | None = None) -> MedicationItem:
    """`medication` is the Medication resource when the request uses medicationReference."""
    if "medicationCodeableConcept" in r:
        med, med_ref = _concept_required(r["medicationCodeableConcept"]), None
    elif medication is not None:
        med, med_ref = _concept_required(medication.get("code")), ref_of(medication)
    else:
        raise ValueError(f"{ref_of(r)} references a Medication that wasn't supplied")
    return MedicationItem(
        ref=ref_of(r),
        medication=med,
        medication_ref=med_ref,
        status=r.get("status"),
        intent=r.get("intent"),
        authored_on=r.get("authoredOn"),
        dosage=[_dosage(d) for d in r.get("dosageInstruction", [])],
    )


def _component(raw: Resource) -> Component:
    return Component(
        code=_concept_required(raw.get("code")),
        value_quantity=quantity(raw.get("valueQuantity")),
        value_concept=concept(raw.get("valueCodeableConcept")),
        value_string=raw.get("valueString"),
    )


def observation_item(r: Resource) -> ObservationItem:
    return ObservationItem(
        ref=ref_of(r),
        code=_concept_required(r.get("code")),
        category=[c for c in (concept(x) for x in r.get("category", [])) if c is not None],
        status=r.get("status"),
        effective=r.get("effectiveDateTime"),
        value_quantity=quantity(r.get("valueQuantity")),
        value_concept=concept(r.get("valueCodeableConcept")),
        value_string=r.get("valueString"),
        components=[_component(c) for c in r.get("component", [])],
    )
