"""Mapping copies values, never computes them (invariant 4). Checked across the whole cohort."""

from __future__ import annotations

from typing import Any

import pytest

from fhir_over_mcp.server import mapping
from fhir_over_mcp.server.models import Concept, Quantity
from tests.cohort_files import by_full_url, patient_bundles, resources


def same_concept(item: Concept | None, raw: dict[str, Any] | None) -> bool:
    if raw is None:
        return item is None
    assert item is not None
    return item.text == raw.get("text") and [
        (c.system, c.code, c.display) for c in item.codings
    ] == [(c.get("system"), c.get("code"), c.get("display")) for c in raw.get("coding", [])]


def same_quantity(item: Quantity | None, raw: dict[str, Any] | None) -> bool:
    if raw is None:
        return item is None
    assert item is not None
    return (item.value, item.unit, item.system, item.code) == (
        raw.get("value"), raw.get("unit"), raw.get("system"), raw.get("code"),
    )  # fmt: skip


def test_every_observation_is_copied() -> None:
    seen = {"valueQuantity": 0, "valueCodeableConcept": 0, "component": 0, "valueString": 0}
    for raw in resources("Observation"):
        item = mapping.observation_item(raw)
        assert item.ref == f"Observation/{raw['id']}"
        assert same_concept(item.code, raw["code"])
        assert item.effective == raw.get("effectiveDateTime")
        assert item.status == raw.get("status")
        assert same_quantity(item.value_quantity, raw.get("valueQuantity"))
        assert same_concept(item.value_concept, raw.get("valueCodeableConcept"))
        assert item.value_string == raw.get("valueString")
        assert len(item.components) == len(raw.get("component", []))
        for got, want in zip(item.components, raw.get("component", []), strict=True):
            assert same_concept(got.code, want["code"])
            assert same_quantity(got.value_quantity, want.get("valueQuantity"))
        for key in seen:
            seen[key] += key in raw
    # The cohort has every value shape the tool claims to handle.
    assert all(seen.values()), seen


def test_every_condition_is_copied() -> None:
    with_abatement = 0
    for raw in resources("Condition"):
        item = mapping.condition_item(raw)
        assert item.ref == f"Condition/{raw['id']}"
        assert same_concept(item.code, raw["code"])
        assert same_concept(item.clinical_status, raw.get("clinicalStatus"))
        assert item.onset == raw.get("onsetDateTime")
        assert item.abatement == raw.get("abatementDateTime")
        with_abatement += item.abatement is not None
    assert with_abatement > 0


def test_every_patient_is_copied() -> None:
    for raw in resources("Patient"):
        item = mapping.patient_item(raw)
        assert item.ref == f"Patient/{raw['id']}"
        assert item.birth_date == raw.get("birthDate")
        assert item.gender == raw.get("gender")
        assert [(n.family, n.given) for n in item.names] == [
            (n.get("family"), n.get("given", [])) for n in raw["name"]
        ]


def test_every_medication_request_is_copied() -> None:
    shapes = {"text": 0, "structured_only": 0, "none": 0, "reference": 0}
    for bundle in patient_bundles():
        for entry in bundle["entry"]:
            raw = entry["resource"]
            if raw["resourceType"] != "MedicationRequest":
                continue
            medication = None
            if "medicationReference" in raw:
                medication = by_full_url(bundle, raw["medicationReference"]["reference"])
                shapes["reference"] += 1
            item = mapping.medication_item(raw, medication)
            assert item.ref == f"MedicationRequest/{raw['id']}"
            assert item.status == raw["status"]
            assert item.authored_on == raw.get("authoredOn")
            if medication is None:
                assert same_concept(item.medication, raw["medicationCodeableConcept"])
                assert item.medication_ref is None
            else:
                assert same_concept(item.medication, medication["code"])
                assert item.medication_ref == f"Medication/{medication['id']}"
            dosages = raw.get("dosageInstruction", [])
            assert len(item.dosage) == len(dosages)
            for got, want in zip(item.dosage, dosages, strict=True):
                assert got.text == want.get("text")
                assert got.timing_repeat == want.get("timing", {}).get("repeat")
            if not dosages:
                shapes["none"] += 1
            elif any("text" in d for d in dosages):
                shapes["text"] += 1
            else:
                shapes["structured_only"] += 1
    assert all(shapes.values()), shapes


def test_missing_medication_resource_is_an_error_not_a_blank() -> None:
    raw = {"resourceType": "MedicationRequest", "id": "1", "status": "active",
           "medicationReference": {"reference": "Medication/9"}}  # fmt: skip
    with pytest.raises(ValueError, match="Medication"):
        mapping.medication_item(raw)


def test_absent_fields_stay_absent() -> None:
    item = mapping.observation_item(
        {"resourceType": "Observation", "id": "x", "code": {"text": "note"}}
    )
    assert item.value_quantity is None
    assert item.value_concept is None
    assert item.effective is None
    assert item.components == []
