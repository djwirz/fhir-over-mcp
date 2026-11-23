"""The tools through the MCP layer, against a fake FHIR server (no HAPI needed).

Covers invariant 5 (empty compared with error), the page cap, input validation, the method audit,
and what the tool listing promises.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from mcp import Client

from fhir_over_mcp.cli import main
from fhir_over_mcp.config import FhirSettings
from fhir_over_mcp.server.app import build_server

pytestmark = pytest.mark.anyio

BASE = "http://fhir.test/fhir"
SETTINGS = FhirSettings(base_url=BASE, allowlist=[BASE], max_pages=2, timeout_seconds=5)

Handler = Callable[[httpx.Request], httpx.Response]


def bundle(resources: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    links = [{"relation": "next", "url": next_url}] if next_url else []
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [{"resource": r, "search": {"mode": "match"}} for r in resources],
        "link": links,
    }


CONDITION = {
    "resourceType": "Condition",
    "id": "c1",
    "code": {"coding": [{"system": "http://snomed.info/sct", "code": "195967001"}]},
    "clinicalStatus": {"coding": [{"code": "active"}]},
    "subject": {"reference": "Patient/p1"},
    "onsetDateTime": "2019-04-02T10:00:00+00:00",
}


class Fake:
    """A FHIR server stand-in that records every request."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = handler

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.handler(request)

        return httpx.MockTransport(handle)


async def connect(fake: Fake) -> AsyncIterator[Client]:
    async with Client(
        build_server(SETTINGS, transport=fake.transport()), raise_exceptions=True
    ) as c:
        yield c


def text(result: Any) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)


# --- the tool listing ----------------------------------------------------------------------------


async def test_lists_five_read_only_tools_without_ctx() -> None:
    fake = Fake(lambda _: httpx.Response(200, json=bundle([])))
    async for c in connect(fake):
        listed = (await c.list_tools()).tools
    assert {t.name for t in listed} == {
        "search_patients",
        "get_patient",
        "list_conditions",
        "list_medications",
        "list_observations",
    }
    for t in listed:
        assert t.annotations is not None
        assert t.annotations.read_only_hint is True
        assert t.annotations.open_world_hint is False
        assert "ctx" not in t.input_schema.get("properties", {})
        assert t.output_schema is not None
        assert t.description
    assert fake.requests == []  # listing touches no FHIR server


# --- invariant 5: empty compared with error ------------------------------------------------------


async def test_no_match_is_a_result_not_an_error() -> None:
    fake = Fake(lambda _: httpx.Response(200, json=bundle([])))
    async for c in connect(fake):
        r = await c.call_tool(
            "list_conditions", {"patient_ref": "Patient/p1", "clinical_status": "active"}
        )
    assert r.is_error is False
    assert r.structured_content is not None
    assert r.structured_content["items"] == []
    assert r.structured_content["matched"] == 0
    assert "No conditions matched for Patient/p1" in r.structured_content["note"]


async def test_unreachable_fhir_is_an_error_not_an_empty_list() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async for c in connect(Fake(down)):
        for name, args in [
            ("search_patients", {"name": "Smith"}),
            ("get_patient", {"patient_ref": "Patient/p1"}),
            ("list_conditions", {"patient_ref": "Patient/p1"}),
            ("list_medications", {"patient_ref": "Patient/p1"}),
            ("list_observations", {"patient_ref": "Patient/p1"}),
        ]:
            r = await c.call_tool(name, args)
            assert r.is_error is True, name
            assert r.structured_content is None, name
            assert "Couldn't get the data" in text(r), name


async def test_server_error_is_reported_with_its_reason() -> None:
    outcome = {"resourceType": "OperationOutcome", "issue": [{"diagnostics": "database is busy"}]}
    async for c in connect(Fake(lambda _: httpx.Response(503, json=outcome))):
        r = await c.call_tool("list_observations", {"patient_ref": "Patient/p1"})
    assert r.is_error is True
    assert "503" in text(r)
    assert "database is busy" in text(r)


async def test_missing_patient_is_an_error() -> None:
    async for c in connect(
        Fake(lambda _: httpx.Response(404, json={"resourceType": "OperationOutcome"}))
    ):
        r = await c.call_tool("get_patient", {"patient_ref": "Patient/nope"})
    assert r.is_error is True
    assert "No Patient/nope on this FHIR server" in text(r)


# --- items and the page cap -----------------------------------------------------------------------


async def test_items_carry_refs_and_copied_values() -> None:
    async for c in connect(Fake(lambda _: httpx.Response(200, json=bundle([CONDITION])))):
        r = await c.call_tool("list_conditions", {"patient_ref": "Patient/p1"})
    assert r.structured_content is not None
    [item] = r.structured_content["items"]
    assert item["ref"] == "Condition/c1"
    assert item["code"]["codings"][0] == {
        "system": "http://snomed.info/sct",
        "code": "195967001",
        "display": None,
    }
    assert item["onset"] == "2019-04-02T10:00:00+00:00"
    assert item["abatement"] is None


async def test_page_cap_is_reported_as_truncated() -> None:
    def pages(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        cond = {**CONDITION, "id": f"c{page}"}
        return httpx.Response(200, json=bundle([cond], f"{BASE}?page={page + 1}"))

    async for c in connect(Fake(pages)):
        r = await c.call_tool("list_conditions", {"patient_ref": "Patient/p1"})
    assert r.structured_content is not None
    assert r.structured_content["truncated"] is True
    assert r.structured_content["matched"] == 2  # max_pages=2
    assert "Narrow the search" in r.structured_content["note"]


async def test_medication_reference_is_resolved_and_both_refs_carried() -> None:
    request = {
        "resourceType": "MedicationRequest", "id": "m1", "status": "active",
        "medicationReference": {"reference": "Medication/med9"},
        "subject": {"reference": "Patient/p1"},
    }  # fmt: skip
    medication = {"resourceType": "Medication", "id": "med9", "code": {"text": "albuterol"}}

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/Medication/med9"):
            return httpx.Response(200, json=medication)
        return httpx.Response(200, json=bundle([request]))

    async for c in connect(Fake(handler)):
        r = await c.call_tool("list_medications", {"patient_ref": "Patient/p1"})
    assert r.structured_content is not None
    [item] = r.structured_content["items"]
    assert item["ref"] == "MedicationRequest/m1"
    assert item["medication_ref"] == "Medication/med9"
    assert item["medication"]["text"] == "albuterol"


async def test_unfollowable_medication_reference_is_an_error() -> None:
    request = {
        "resourceType": "MedicationRequest", "id": "m1", "status": "active",
        "medicationReference": {"reference": "https://elsewhere.example/Medication/1"},
    }  # fmt: skip
    async for c in connect(Fake(lambda _: httpx.Response(200, json=bundle([request])))):
        r = await c.call_tool("list_medications", {"patient_ref": "Patient/p1"})
    assert r.is_error is True
    assert "can't follow" in text(r)


async def test_latest_only_asks_the_server_for_one_by_date() -> None:
    fake = Fake(lambda _: httpx.Response(200, json=bundle([], f"{BASE}?page=2")))
    async for c in connect(fake):
        r = await c.call_tool(
            "list_observations",
            {"patient_ref": "Patient/p1", "code": "http://loinc.org|19926-5", "latest_only": True},
        )
    assert r.structured_content is not None
    assert r.structured_content["truncated"] is False
    [req] = fake.requests
    assert req.url.params["_sort"] == "-date"
    assert req.url.params["_count"] == "1"


async def test_date_range_is_sent_as_two_date_params() -> None:
    fake = Fake(lambda _: httpx.Response(200, json=bundle([])))
    async for c in connect(fake):
        await c.call_tool(
            "list_observations",
            {"patient_ref": "Patient/p1", "date_from": "2024-01-01", "date_to": "2024-12-31"},
        )
    assert fake.requests[0].url.params.get_list("date") == ["ge2024-01-01", "le2024-12-31"]


# --- validation: rejected before any request ----------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_patient", {"patient_ref": "p1"}),
        ("get_patient", {"patient_ref": "Patient/../../admin"}),
        ("get_patient", {"patient_ref": "Condition/1"}),
        ("list_conditions", {"patient_ref": "Patient/p1", "clinical_status": "sort-of"}),
        ("list_conditions", {"patient_ref": "Patient/p1", "code": "has spaces"}),
        ("list_observations", {"patient_ref": "Patient/p1", "date_from": "last tuesday"}),
        (
            "list_observations",
            {"patient_ref": "Patient/p1", "date_from": "2024-02-01", "date_to": "2024-01-01"},
        ),
        ("list_observations", {"patient_ref": "Patient/p1", "latest_only": True}),
        ("search_patients", {}),
        ("search_patients", {"name": "x" * 101}),
    ],
)
async def test_bad_input_is_rejected_before_fhir_is_called(tool: str, args: dict[str, Any]) -> None:
    fake = Fake(lambda _: httpx.Response(200, json=bundle([])))
    async for c in connect(fake):
        r = await c.call_tool(tool, args)
    assert r.is_error is True
    assert fake.requests == []


# --- invariant 1 through the server ---------------------------------------------------------------


async def test_every_tool_sends_only_get() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/Patient/p1"):
            return httpx.Response(200, json={"resourceType": "Patient", "id": "p1"})
        if r.url.path.endswith("/Patient/gone"):
            return httpx.Response(404)
        return httpx.Response(200, json=bundle([CONDITION]))

    fake = Fake(handler)
    async for c in connect(fake):
        await c.call_tool("search_patients", {"name": "Smith", "condition_code": "195967001"})
        await c.call_tool("get_patient", {"patient_ref": "Patient/p1"})
        await c.call_tool("get_patient", {"patient_ref": "Patient/gone"})
        await c.call_tool("list_conditions", {"patient_ref": "Patient/p1"})
        await c.call_tool("list_medications", {"patient_ref": "Patient/p1"})
        await c.call_tool(
            "list_observations", {"patient_ref": "Patient/p1", "code": "x", "latest_only": True}
        )
    assert len(fake.requests) >= 6
    assert {r.method for r in fake.requests} == {"GET"}
    assert {r.url.host for r in fake.requests} == {"fhir.test"}


# --- invariant 2: refuses to start ---------------------------------------------------------------


def test_serve_refuses_a_base_off_the_allowlist(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["serve", "--fhir-base", "https://elsewhere.example/fhir"]) == 2
    assert "not on the FHIR allowlist" in capsys.readouterr().err
