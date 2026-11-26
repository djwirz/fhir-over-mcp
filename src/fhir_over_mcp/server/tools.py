"""The five tools as plain functions over the read-only client (ADR 0002, spec §4).

`app.py` registers them with MCP. Keeping them plain makes them testable without a protocol, and
keeps the rules in one place:
- every item carries its resource's ref and copied values (invariant 4, see `mapping.py`);
- "nothing matched" is a normal result with `matched == 0` and a note saying so, while "couldn't
  get the data" raises `ToolError` (invariant 5);
- a search cut short by the page cap says `truncated` and asks for a narrower search.

In patient-context mode `client` is a `ScopedReader` (`scope.py`), which does the guarding; here
the only difference is that the note says when something was withheld.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from typing import Annotated, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import AfterValidator, Field

from fhir_over_mcp.fhir import (
    FhirError,
    FhirHttpError,
    FhirUnavailable,
    NotAllowlisted,
    NotFound,
    SearchResult,
)
from fhir_over_mcp.server import mapping
from fhir_over_mcp.server.models import (
    ConditionList,
    MedicationItem,
    MedicationList,
    ObservationList,
    PatientItem,
    PatientList,
)
from fhir_over_mcp.server.scope import Reader, ScopedReader

PAGE_SIZE = "50"


def _not_a_dot_segment(patient_ref: str) -> str:
    # "." and ".." fit the id grammar, but a URL collapses them into a different path. Checked in
    # code, not in the pattern, so the input schema the agent sees stays the same.
    if _patient_id(patient_ref) in (".", ".."):
        raise ValueError("not a patient id: '.' and '..' are path segments")
    return patient_ref


# FHIR ids: 1-64 of [A-Za-z0-9-.]. https://hl7.org/fhir/R4/datatypes.html#id
PatientRef = Annotated[
    str,
    Field(
        pattern=r"^Patient/[A-Za-z0-9\-.]{1,64}$",
        description="The patient's reference, e.g. Patient/123, as returned by search_patients.",
    ),
    AfterValidator(_not_a_dot_segment),
]
# A FHIR token: `system|code` or a bare code.
Code = Annotated[
    str,
    Field(
        pattern=r"^([^|\s]{1,200}\|)?[^|\s]{1,100}$",
        description="A code, optionally with its system: `http://loinc.org|19926-5` or `19926-5`.",
    ),
]
ConditionClinicalStatus = Literal[
    "active", "recurrence", "relapse", "inactive", "remission", "resolved"
]
MedicationRequestStatus = Literal[
    "active", "on-hold", "cancelled", "completed", "entered-in-error", "stopped", "draft", "unknown"
]
ObservationCategory = Literal[
    "social-history", "vital-signs", "imaging", "laboratory", "procedure", "survey", "exam",
    "therapy", "activity",
]  # fmt: skip


@contextmanager
def _fhir_errors() -> Iterator[None]:
    """Turn client errors into tool errors the model can read. Never an empty result."""
    try:
        yield
    except NotAllowlisted as e:
        raise ToolError(f"Refused: {e.url} is not on this server's FHIR allowlist.") from e
    except FhirUnavailable as e:
        raise ToolError(f"Couldn't get the data: the FHIR server didn't respond ({e}).") from e
    except FhirHttpError as e:
        raise ToolError(
            f"Couldn't get the data: the FHIR server returned {e.status}: {e.detail}"
        ) from e
    except FhirError as e:
        raise ToolError(f"Couldn't get the data: {e}") from e


def _patient_id(patient_ref: str) -> str:
    return patient_ref.split("/", 1)[1]


def _describe(filters: Mapping[str, object]) -> str:
    shown = [f"{k}={v}" for k, v in filters.items() if v not in (None, False)]
    return f" with {', '.join(shown)}" if shown else ""


def _note(
    kind: str, result: SearchResult, count: int, subject: str, filters: Mapping[str, object]
) -> str:
    """`kind` is plural ("conditions"); its singular is `kind` without the final "s"."""
    where = f"{subject}{_describe(filters)}"
    if result.withheld:
        found = _note(kind, replace(result, withheld=0), count, subject, filters)
        return (
            f"{found} {result.withheld} more from the FHIR server couldn't be confirmed as this "
            "patient's and were withheld, so this list may be incomplete."
        )
    if count == 0:
        return f"No {kind} matched for {where}. The search succeeded; the record has none."
    if result.truncated:
        return (
            f"Showing the first {count} {kind} for {where}; more matched. "
            "Narrow the search to see the rest."
        )
    return f"{count} {kind if count != 1 else kind[:-1]} for {where}."


def search_patients(
    client: Reader,
    name: str | None = None,
    birth_date: date | None = None,
    condition_code: str | None = None,
) -> PatientList:
    if not (name or birth_date or condition_code):
        raise ToolError("Give at least one of name, birth_date or condition_code.")
    params: dict[str, str] = {"_count": PAGE_SIZE}
    if name:
        params["name"] = name
    if birth_date:
        params["birthdate"] = birth_date.isoformat()
    if condition_code:
        params["_has:Condition:patient:code"] = condition_code
    with _fhir_errors():
        result = client.search("Patient", params)
    items = [
        mapping.patient_item(r) for r in result.resources if r.get("resourceType") == "Patient"
    ]
    filters = {"name": name, "birth_date": birth_date, "condition_code": condition_code}
    note = _note("patients", result, len(items), "the search", filters)
    if isinstance(client, ScopedReader) and not items:
        # "No patients matched" alone would read as "there is no such patient".
        note = (
            f"This server is bound to {client.scope.ref}, so only that patient was searched. "
            f"They don't match{_describe(filters).removeprefix(' with')}."
        )
    return PatientList(items=items, matched=len(items), truncated=result.truncated, note=note)


def get_patient(client: Reader, patient_ref: str) -> PatientItem:
    with _fhir_errors():
        try:
            resource = client.read("Patient", _patient_id(patient_ref))
        except NotFound as e:
            raise ToolError(f"No {patient_ref} on this FHIR server.") from e
    if f"{resource.get('resourceType')}/{resource.get('id')}" != patient_ref:
        # Not quoted: whatever came back isn't what was asked for.
        raise ToolError(f"Couldn't get the data: the FHIR server didn't answer with {patient_ref}.")
    return mapping.patient_item(resource)


def list_conditions(
    client: Reader,
    patient_ref: str,
    clinical_status: str | None = None,
    code: str | None = None,
) -> ConditionList:
    params = {"patient": _patient_id(patient_ref), "_count": PAGE_SIZE}
    if clinical_status:
        params["clinical-status"] = clinical_status
    if code:
        params["code"] = code
    with _fhir_errors():
        result = client.search("Condition", params)
    items = [
        mapping.condition_item(r) for r in result.resources if r.get("resourceType") == "Condition"
    ]
    filters = {"clinical_status": clinical_status, "code": code}
    return ConditionList(
        items=items,
        matched=len(items),
        truncated=result.truncated,
        note=_note("conditions", result, len(items), patient_ref, filters),
    )


_MEDICATION_REF = re.compile(r"^Medication/([A-Za-z0-9\-.]{1,64})$")


def list_medications(client: Reader, patient_ref: str, status: str | None = None) -> MedicationList:
    params = {"patient": _patient_id(patient_ref), "_count": PAGE_SIZE}
    if status:
        params["status"] = status
    items: list[MedicationItem] = []
    with _fhir_errors():
        result = client.search("MedicationRequest", params)
        for r in result.resources:
            if r.get("resourceType") != "MedicationRequest":
                continue
            medication = None
            if "medicationCodeableConcept" not in r:
                reference = r.get("medicationReference", {}).get("reference", "")
                match = _MEDICATION_REF.match(reference)
                if not match:
                    raise ToolError(
                        f"Couldn't get the data: MedicationRequest/{r.get('id')} names its "
                        f"medication by a reference this server can't follow ({reference!r})."
                    )
                medication = client.read("Medication", match.group(1))
            items.append(mapping.medication_item(r, medication))
    return MedicationList(
        items=items,
        matched=len(items),
        truncated=result.truncated,
        note=_note("medication requests", result, len(items), patient_ref, {"status": status}),
    )


def list_observations(
    client: Reader,
    patient_ref: str,
    code: str | None = None,
    category: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    latest_only: bool = False,
) -> ObservationList:
    if latest_only and not code:
        raise ToolError("latest_only needs a code, so 'latest' means the latest of one thing.")
    if date_from and date_to and date_from > date_to:
        raise ToolError("date_from is after date_to.")
    params: dict[str, str | list[str]] = {"patient": _patient_id(patient_ref), "_count": PAGE_SIZE}
    if code:
        params["code"] = code
    if category:
        params["category"] = category
    dates = []
    if date_from:
        dates.append(f"ge{date_from.isoformat()}")
    if date_to:
        dates.append(f"le{date_to.isoformat()}")
    if dates:
        params["date"] = dates
    if latest_only:
        params["_sort"] = "-date"
        params["_count"] = "1"
    with _fhir_errors():
        result = client.search("Observation", params, max_pages=1 if latest_only else None)
    items = [
        mapping.observation_item(r)
        for r in result.resources
        if r.get("resourceType") == "Observation"
    ]
    truncated = False if latest_only else result.truncated
    filters = {
        "code": code, "category": category, "date_from": date_from, "date_to": date_to,
        "latest_only": latest_only,
    }  # fmt: skip
    shown = replace(result, truncated=truncated)
    return ObservationList(
        items=items,
        matched=len(items),
        truncated=truncated,
        note=_note("observations", shown, len(items), patient_ref, filters),
    )


TOOLS: dict[str, Callable[..., object]] = {
    "search_patients": search_patients,
    "get_patient": get_patient,
    "list_conditions": list_conditions,
    "list_medications": list_medications,
    "list_observations": list_observations,
}
