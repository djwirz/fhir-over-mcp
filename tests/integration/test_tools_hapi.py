"""The five tools against HAPI loaded with the cohort.

Expected values come from the bundle files, never from the server (the habit invariant 6 needs).
HAPI assigns its own ids, so results are matched on recorded content (codes, dates, values), and
every returned ref is read back from HAPI to check it really is that resource.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from mcp import Client

from fhir_over_mcp.cohort.check import ASTHMA_CODES, COPD_CODES
from fhir_over_mcp.config import FhirSettings
from fhir_over_mcp.server.app import build_server
from tests.cohort_files import by_full_url, patient_bundles

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SYNTHEA_ID = "https://github.com/synthetichealth/synthea"
FEV1_FVC = "http://loinc.org|19926-5"
BLOOD_PRESSURE = "http://loinc.org|85354-9"
Resource = dict[str, Any]


def _codes(concept: Resource) -> frozenset[tuple[str | None, str | None]]:
    return frozenset((c.get("system"), c.get("code")) for c in concept.get("coding", []))


def _of(bundle: Resource, kind: str) -> list[Resource]:
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == kind]


def _patient(bundle: Resource) -> Resource:
    [p] = _of(bundle, "Patient")
    return p


def _has_condition(bundle: Resource, codes: frozenset[str]) -> bool:
    return any(
        c.get("code") in codes for r in _of(bundle, "Condition") for c in r["code"]["coding"]
    )


def _pick(predicate: Any) -> Resource:
    for b in patient_bundles():
        if predicate(b):
            return b
    raise AssertionError("no patient in the cohort fits")


COPD = _pick(lambda b: _has_condition(b, COPD_CODES))
ASTHMA = _pick(lambda b: _has_condition(b, ASTHMA_CODES))
GENERAL = _pick(lambda b: not _has_condition(b, ASTHMA_CODES | COPD_CODES))
WITH_MED_REF = _pick(lambda b: any("medicationReference" in r for r in _of(b, "MedicationRequest")))


def _server_ref(base: str, bundle: Resource) -> str:
    synthea_id = _patient(bundle)["id"]
    r = httpx.get(
        f"{base}/Patient", params={"identifier": f"{SYNTHEA_ID}|{synthea_id}"}, timeout=30
    )
    r.raise_for_status()
    [entry] = r.json()["entry"]
    return f"Patient/{entry['resource']['id']}"


def _read(base: str, ref: str) -> Resource:
    r = httpx.get(f"{base}/{ref}", timeout=30)
    r.raise_for_status()
    body: Resource = r.json()
    return body


@pytest.fixture
async def client(fhir_base: str) -> AsyncIterator[Client]:
    settings = FhirSettings(base_url=fhir_base, allowlist=[fhir_base], max_pages=10)
    async with Client(build_server(settings), raise_exceptions=True) as c:
        yield c


async def _call(client: Client, tool: str, args: dict[str, Any]) -> Resource:
    result = await client.call_tool(tool, args)
    assert result.is_error is False, result.content
    content: Resource | None = result.structured_content
    assert content is not None
    return content


def _item_codes(item: Resource, key: str = "code") -> frozenset[tuple[str | None, str | None]]:
    return frozenset((c["system"], c["code"]) for c in item[key]["codings"])


@pytest.mark.parametrize("bundle", [COPD, ASTHMA, GENERAL], ids=["copd", "asthma", "general"])
async def test_conditions_match_the_bundle(
    client: Client, fhir_base: str, bundle: Resource
) -> None:
    ref = _server_ref(fhir_base, bundle)
    got = await _call(client, "list_conditions", {"patient_ref": ref})
    assert got["truncated"] is False
    want = Counter(
        (_codes(r["code"]), r.get("onsetDateTime"), r.get("abatementDateTime"))
        for r in _of(bundle, "Condition")
    )
    have = Counter((_item_codes(i), i["onset"], i["abatement"]) for i in got["items"])
    assert have == want
    for item in got["items"][:5]:
        resource = _read(fhir_base, item["ref"])
        assert resource["subject"]["reference"] == ref
        assert resource["onsetDateTime"] == item["onset"]


@pytest.mark.parametrize("bundle", [COPD, ASTHMA, GENERAL], ids=["copd", "asthma", "general"])
async def test_medications_match_the_bundle(
    client: Client, fhir_base: str, bundle: Resource
) -> None:
    ref = _server_ref(fhir_base, bundle)
    got = await _call(client, "list_medications", {"patient_ref": ref})
    if got["truncated"]:
        pytest.skip("more requests than the page cap; covered by the truncation test")

    def med_codes(r: Resource) -> frozenset[tuple[str | None, str | None]]:
        if "medicationCodeableConcept" in r:
            return _codes(r["medicationCodeableConcept"])
        return _codes(by_full_url(bundle, r["medicationReference"]["reference"])["code"])

    want = Counter(
        (
            med_codes(r),
            r.get("authoredOn"),
            r["status"],
            tuple(d.get("text") for d in r.get("dosageInstruction", [])),
        )
        for r in _of(bundle, "MedicationRequest")
    )
    have = Counter(
        (
            _item_codes(i, "medication"),
            i["authored_on"],
            i["status"],
            tuple(d["text"] for d in i["dosage"]),
        )
        for i in got["items"]
    )
    assert have == want


async def test_medication_reference_is_followed(client: Client, fhir_base: str) -> None:
    ref = _server_ref(fhir_base, WITH_MED_REF)
    got = await _call(client, "list_medications", {"patient_ref": ref})
    referenced = [i for i in got["items"] if i["medication_ref"]]
    assert referenced
    for item in referenced:
        medication = _read(fhir_base, item["medication_ref"])
        assert _codes(medication["code"]) == _item_codes(item, "medication")
        request = _read(fhir_base, item["ref"])
        assert request["medicationReference"]["reference"] == item["medication_ref"]


async def test_lung_function_matches_the_bundle(client: Client, fhir_base: str) -> None:
    ref = _server_ref(fhir_base, COPD)
    got = await _call(client, "list_observations", {"patient_ref": ref, "code": FEV1_FVC})
    want = sorted(
        (r["effectiveDateTime"], r["valueQuantity"]["value"], r["valueQuantity"]["unit"])
        for r in _of(COPD, "Observation")
        if ("http://loinc.org", "19926-5") in _codes(r["code"])
    )
    have = sorted(
        (i["effective"], i["value_quantity"]["value"], i["value_quantity"]["unit"])
        for i in got["items"]
    )
    assert want
    assert have == want

    latest = await _call(
        client, "list_observations", {"patient_ref": ref, "code": FEV1_FVC, "latest_only": True}
    )
    [item] = latest["items"]
    assert (
        item["effective"],
        item["value_quantity"]["value"],
        item["value_quantity"]["unit"],
    ) == want[-1]
    assert _read(fhir_base, item["ref"])["valueQuantity"]["value"] == want[-1][1]


async def test_blood_pressure_components_match(client: Client, fhir_base: str) -> None:
    ref = _server_ref(fhir_base, GENERAL)
    got = await _call(client, "list_observations", {"patient_ref": ref, "code": BLOOD_PRESSURE})

    def parts(components: list[Resource], code_key: str, value_key: str) -> frozenset[Any]:
        """Raw components use `coding`; tool items use `codings`. Same fields inside."""
        out = set()
        for comp in components:
            concept = comp[code_key]
            codings = concept.get("codings", concept.get("coding", []))
            codes = frozenset((c["system"], c["code"]) for c in codings)
            out.add((codes, comp[value_key]["value"], comp[value_key]["unit"]))
        return frozenset(out)

    want = Counter(
        (r["effectiveDateTime"], parts(r["component"], "code", "valueQuantity"))
        for r in _of(GENERAL, "Observation")
        if ("http://loinc.org", "85354-9") in _codes(r["code"])
    )
    have = Counter(
        (i["effective"], parts(i["components"], "code", "value_quantity")) for i in got["items"]
    )
    assert want
    assert have == want


async def test_get_patient_matches_the_bundle(client: Client, fhir_base: str) -> None:
    ref = _server_ref(fhir_base, ASTHMA)
    got = await _call(client, "get_patient", {"patient_ref": ref})
    raw = _patient(ASTHMA)
    assert got["ref"] == ref
    assert got["birth_date"] == raw["birthDate"]
    assert got["gender"] == raw["gender"]
    assert [n["family"] for n in got["names"]] == [n.get("family") for n in raw["name"]]


async def test_search_patients_finds_them_by_name_birth_date_and_condition(
    client: Client, fhir_base: str
) -> None:
    raw = _patient(COPD)
    ref = _server_ref(fhir_base, COPD)
    by_name = await _call(client, "search_patients", {"name": raw["name"][0]["family"]})
    assert ref in {i["ref"] for i in by_name["items"]}
    by_birth = await _call(client, "search_patients", {"birth_date": raw["birthDate"]})
    assert ref in {i["ref"] for i in by_birth["items"]}

    copd_codes = [
        c for r in _of(COPD, "Condition") for c in r["code"]["coding"] if c["code"] in COPD_CODES
    ]
    code = f"{copd_codes[0]['system']}|{copd_codes[0]['code']}"
    by_condition = await _call(client, "search_patients", {"condition_code": code})
    want = {
        _server_ref(fhir_base, b)
        for b in patient_bundles()
        if any((c.get("system"), c.get("code")) == (copd_codes[0]["system"], copd_codes[0]["code"])
               for r in _of(b, "Condition") for c in r["code"]["coding"])
    }  # fmt: skip
    assert {i["ref"] for i in by_condition["items"]} == want


async def test_absent_data_is_an_honest_empty(client: Client, fhir_base: str) -> None:
    ref = _server_ref(fhir_base, GENERAL)
    got = await _call(
        client, "list_conditions", {"patient_ref": ref, "code": "http://snomed.info/sct|87433001"}
    )
    assert got["items"] == []
    assert got["matched"] == 0
    assert "No conditions matched" in got["note"]


async def test_page_cap_against_a_real_patient(fhir_base: str) -> None:
    ref = _server_ref(fhir_base, COPD)
    settings = FhirSettings(base_url=fhir_base, allowlist=[fhir_base], max_pages=1)
    async with Client(build_server(settings), raise_exceptions=True) as c:
        got = await _call(c, "list_observations", {"patient_ref": ref})
    total = len(_of(COPD, "Observation"))
    assert total > 50
    assert got["truncated"] is True
    assert got["matched"] == 50


def _recording_transport(methods: list[str]) -> httpx.HTTPTransport:
    class Recording(httpx.HTTPTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            return super().handle_request(request)

    return Recording()


async def test_method_audit_through_every_tool(fhir_base: str) -> None:
    """Invariant 1 end to end: whatever the tools do, only GET reaches the FHIR server."""
    methods: list[str] = []
    settings = FhirSettings(base_url=fhir_base, allowlist=[fhir_base], max_pages=2)
    ref = _server_ref(fhir_base, WITH_MED_REF)
    async with Client(
        build_server(settings, transport=_recording_transport(methods)), raise_exceptions=True
    ) as c:
        await c.call_tool("search_patients", {"condition_code": "http://snomed.info/sct|195967001"})
        await c.call_tool("get_patient", {"patient_ref": ref})
        await c.call_tool("get_patient", {"patient_ref": "Patient/does-not-exist"})
        await c.call_tool("list_conditions", {"patient_ref": ref})
        await c.call_tool("list_medications", {"patient_ref": ref})
        await c.call_tool(
            "list_observations", {"patient_ref": ref, "code": FEV1_FVC, "latest_only": True}
        )
        await c.call_tool("list_observations", {"patient_ref": ref, "date_from": "2024-01-01"})
    assert len(methods) >= 7
    assert set(methods) == {"GET"}
