"""The read-only client against real HAPI loaded with the cohort."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from synthetic_cohort import bundle_dir
from synthetic_cohort.check import COPD_CODES, summarise_bundle

from fhir_over_mcp.fhir import NotFound, ReadOnlyFhirClient

pytestmark = pytest.mark.integration

SYNTHEA_ID_SYSTEM = "https://github.com/synthetichealth/synthea"


def _first_copd_patient() -> tuple[str, int]:
    """A COPD patient's Synthea id and their FEV1/FVC count, read from the files, not the server."""
    for path in sorted(bundle_dir().glob("*.json")):
        bundle = json.loads(path.read_text())
        summary = summarise_bundle(bundle)
        if summary and summary.copd and summary.lung_function_obs:
            fev = sum(
                1
                for e in bundle["entry"]
                if e["resource"]["resourceType"] == "Observation"
                and any(c.get("code") == "19926-5" for c in e["resource"]["code"]["coding"])
            )
            return summary.ref.split("/")[1], fev
    raise AssertionError("cohort has no COPD patient with lung-function readings")


@pytest.fixture
def methods() -> list[str]:
    return []


@pytest.fixture
def client(fhir_base: str, methods: list[str]) -> Iterator[ReadOnlyFhirClient]:
    """The real client, with the real HTTP transport wrapped to record every method sent."""

    class Recording(httpx.HTTPTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            return super().handle_request(request)

    with ReadOnlyFhirClient(fhir_base, [fhir_base], max_pages=3, transport=Recording()) as c:
        yield c


def _server_id(client: ReadOnlyFhirClient, synthea_id: str) -> str:
    found = client.search("Patient", {"identifier": f"{SYNTHEA_ID_SYSTEM}|{synthea_id}"})
    assert len(found.resources) == 1
    return str(found.resources[0]["id"])


def test_reads_a_known_patient(client: ReadOnlyFhirClient) -> None:
    synthea_id, _ = _first_copd_patient()
    pid = _server_id(client, synthea_id)
    patient = client.read("Patient", pid)
    assert patient["resourceType"] == "Patient"
    assert patient["id"] == pid


def test_copd_patient_has_their_lung_function_readings(client: ReadOnlyFhirClient) -> None:
    synthea_id, expected = _first_copd_patient()
    pid = _server_id(client, synthea_id)
    conditions = client.search("Condition", {"patient": pid})
    codes = {c["code"] for r in conditions.resources for c in r["code"]["coding"]}
    assert codes & COPD_CODES
    obs = client.search(
        "Observation", {"patient": pid, "code": "http://loinc.org|19926-5"}, max_pages=20
    )
    assert not obs.truncated
    assert len(obs.resources) == expected
    assert all(o["subject"]["reference"] == f"Patient/{pid}" for o in obs.resources)


def test_paging_cap_against_a_real_search(client: ReadOnlyFhirClient) -> None:
    result = client.search("Observation", {"_count": "5"}, max_pages=2)
    assert len(result.resources) == 10
    assert result.truncated is True


def test_missing_resource_is_not_found_not_empty(client: ReadOnlyFhirClient) -> None:
    with pytest.raises(NotFound):
        client.read("Patient", "does-not-exist")


def test_no_match_is_an_honest_empty(client: ReadOnlyFhirClient) -> None:
    result = client.search("Patient", {"identifier": f"{SYNTHEA_ID_SYSTEM}|no-such-patient"})
    assert result.resources == []
    assert result.truncated is False


def test_only_get_reached_the_server(client: ReadOnlyFhirClient, methods: list[str]) -> None:
    synthea_id, _ = _first_copd_patient()
    pid = _server_id(client, synthea_id)
    client.read("Patient", pid)
    client.search("Observation", {"patient": pid, "_count": "5"}, max_pages=2)
    with pytest.raises(NotFound):
        client.read("Patient", "does-not-exist")
    assert methods
    assert set(methods) == {"GET"}
