"""Patient-context mode against HAPI loaded with the cohort (invariant 3).

A correct FHIR server never returns another patient's records to a `patient=` search, so to reach
the output check these tests seed foreign records and then loosen the outgoing filter, standing in
for a server that applies it wrongly. The seeding and clean-up write to HAPI directly; nothing in
the server can.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from mcp import Client, StdioServerParameters
from synthetic_cohort.check import ASTHMA_CODES

from fhir_over_mcp.config import FhirSettings
from fhir_over_mcp.server.app import build_server
from fhir_over_mcp.server.scope import PatientScope
from tests.integration.test_protocol_stdio import REPO
from tests.integration.test_tools_hapi import (
    ASTHMA,
    COPD,
    _of,
    _patient,
    _server_ref,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MARK = "ZZFOREIGN"
CODE = {"coding": [{"system": "http://example.org/scope-test", "code": MARK}], "text": MARK}
Resource = dict[str, Any]


def _id(ref: str) -> str:
    return ref.split("/")[1]


def _first_id(base: str, kind: str, patient_ref: str) -> str:
    r = httpx.get(f"{base}/{kind}", params={"patient": _id(patient_ref), "_count": "1"}, timeout=30)
    r.raise_for_status()
    return str(r.json()["entry"][0]["resource"]["id"])


@dataclass
class Seeded:
    base: str
    bound: str
    patient: str
    medication: str
    refs: dict[str, str]  # resource type -> the foreign resource of that type
    subjectless: str


@pytest.fixture(scope="module")
def seeded(fhir_base: str) -> Iterator[Seeded]:
    """A foreign patient with one of everything, plus an Observation with no subject at all."""
    created: list[str] = []

    def create(resource: Resource) -> str:
        r = httpx.post(f"{fhir_base}/{resource['resourceType']}", json=resource, timeout=30)
        r.raise_for_status()
        ref = f"{resource['resourceType']}/{r.json()['id']}"
        created.append(ref)
        return ref

    try:
        patient = create({"resourceType": "Patient", "name": [{"family": MARK}]})
        subject = {"reference": patient}
        medication = create({"resourceType": "Medication", "code": CODE})
        condition = create({"resourceType": "Condition", "code": CODE, "subject": subject})
        request = create(
            {
                "resourceType": "MedicationRequest",
                "status": "active",
                "intent": "order",
                "subject": subject,
                "medicationReference": {"reference": medication},
            }
        )
        observed: Resource = {"resourceType": "Observation", "status": "final", "code": CODE}
        observation = create({**observed, "subject": subject, "valueString": MARK})
        subjectless = create({**observed, "valueString": f"{MARK} nobody"})
        yield Seeded(
            base=fhir_base,
            bound=_server_ref(fhir_base, ASTHMA),
            patient=patient,
            medication=medication,
            refs={
                "Condition": condition,
                "MedicationRequest": request,
                "Observation": observation,
            },
            subjectless=subjectless,
        )
    finally:
        # Referrers first; later tests compare the server with the cohort files.
        for ref in reversed(created):
            httpx.delete(f"{fhir_base}/{ref}", timeout=30).raise_for_status()


class Loose(httpx.HTTPTransport):
    """Swaps the patient filter for one that also matches the seeded records."""

    def __init__(self, seeded: Seeded, *, include_mine: bool) -> None:
        super().__init__()
        self.seeded = seeded
        self.include_mine = include_mine
        self.paths: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        kind = request.url.path.rsplit("/", 1)[-1]
        s = self.seeded
        ids: list[str] = []
        if kind == "Patient" and "_id" in request.url.params:
            ids = [_id(s.patient)]
            mine = _id(s.bound)
        elif kind in s.refs:
            ids = [_id(s.refs[kind])]
            if kind == "Observation":
                ids.append(_id(s.subjectless))
            mine = _first_id(s.base, kind, s.bound) if self.include_mine else ""
        if ids:
            if self.include_mine:
                ids.insert(0, mine)
            params = request.url.params.remove("patient").remove("name")
            request.url = request.url.copy_with(params=params.set("_id", ",".join(ids)))
        return super().handle_request(request)


def _settings(base: str) -> FhirSettings:
    return FhirSettings(base_url=base, allowlist=[base], max_pages=10)


def _everything(result: Any) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content) + json.dumps(
        result.structured_content
    )


def _foreign(seeded: Seeded) -> list[str]:
    refs = [seeded.patient, seeded.medication, seeded.subjectless, *seeded.refs.values()]
    return [MARK, *(f'"{r}"' for r in refs)]


def _assert_no_leak(seeded: Seeded, result: Any) -> None:
    shown = _everything(result)
    for needle in _foreign(seeded):
        assert needle not in shown


LISTS = [
    ("list_conditions", "Condition", 1),
    ("list_medications", "MedicationRequest", 1),
    ("list_observations", "Observation", 2),
]


@pytest.mark.parametrize(("tool", "kind", "foreign"), LISTS)
async def test_seeded_foreign_records_are_dropped(
    seeded: Seeded, tool: str, kind: str, foreign: int
) -> None:
    scope = PatientScope(seeded.bound)
    transport = Loose(seeded, include_mine=True)
    server = build_server(_settings(seeded.base), scope=scope, transport=transport)
    async with Client(server) as c:
        r = await c.call_tool(tool, {"patient_ref": seeded.bound})
    assert r.is_error is False, r.content
    assert r.structured_content is not None
    [item] = r.structured_content["items"]
    assert item["ref"] == f"{kind}/{_first_id(seeded.base, kind, seeded.bound)}"
    assert f"{foreign} more" in r.structured_content["note"]
    assert scope.events == {"dropped": foreign}
    _assert_no_leak(seeded, r)
    assert not any(seeded.medication in p for p in transport.paths)


@pytest.mark.parametrize(("tool", "kind", "foreign"), LISTS)
async def test_only_foreign_records_is_an_error(
    seeded: Seeded, tool: str, kind: str, foreign: int
) -> None:
    scope = PatientScope(seeded.bound)
    server = build_server(
        _settings(seeded.base), scope=scope, transport=Loose(seeded, include_mine=False)
    )
    async with Client(server) as c:
        r = await c.call_tool(tool, {"patient_ref": seeded.bound})
    assert r.is_error is True
    assert scope.events == {"dropped": foreign}
    _assert_no_leak(seeded, r)


async def test_a_seeded_foreign_patient_is_dropped_from_search(seeded: Seeded) -> None:
    scope = PatientScope(seeded.bound)
    server = build_server(
        _settings(seeded.base), scope=scope, transport=Loose(seeded, include_mine=True)
    )
    async with Client(server) as c:
        r = await c.call_tool("search_patients", {"name": MARK})
    assert r.structured_content is not None
    assert [i["ref"] for i in r.structured_content["items"]] == [seeded.bound]
    assert scope.events == {"dropped": 1}
    # The name asked for is echoed in the note; the other patient's record isn't returned.
    assert MARK not in json.dumps(r.structured_content["items"])
    assert f'"{seeded.patient}"' not in _everything(r)


async def test_bound_server_asked_for_other_patients(fhir_base: str, seeded: Seeded) -> None:
    """HAPI behaving normally: refusals on the way in, filters that hold, only GET on the wire."""
    bound, other = seeded.bound, _server_ref(fhir_base, COPD)
    methods: list[str] = []

    class Recording(httpx.HTTPTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            return super().handle_request(request)

    scope = PatientScope(bound)
    server = build_server(_settings(fhir_base), scope=scope, transport=Recording())
    them = _patient(COPD)
    async with Client(server) as c:
        calls: list[tuple[str, dict[str, Any]]] = [
            ("get_patient", {}),
            ("list_conditions", {}),
            ("list_medications", {"status": "active"}),
            ("list_observations", {"code": "http://loinc.org|19926-5", "latest_only": True}),
        ]
        for target in (other, seeded.patient):
            for tool, args in calls:
                r = await c.call_tool(tool, {"patient_ref": target, **args})
                assert r.is_error is True
                assert "Refused" in _everything(r)
        assert methods == []
        assert scope.events == {"refused": 8}

        for args in [
            {"name": them["name"][0]["family"]},
            {"birth_date": them["birthDate"]},
            {"condition_code": "http://snomed.info/sct|87433001"},  # emphysema: the COPD patient's
            {"name": MARK},
        ]:
            r = await c.call_tool("search_patients", args)
            assert r.is_error is False, r.content
            assert r.structured_content is not None
            assert {i["ref"] for i in r.structured_content["items"]} <= {bound}

        me = _patient(ASTHMA)
        my_asthma = next(
            coding
            for r in _of(ASTHMA, "Condition")
            for coding in r["code"]["coding"]
            if coding["code"] in ASTHMA_CODES
        )
        r = await c.call_tool(
            "search_patients",
            {
                "name": me["name"][0]["family"],
                "birth_date": me["birthDate"],
                "condition_code": f"{my_asthma['system']}|{my_asthma['code']}",
            },
        )
        assert r.structured_content is not None
        assert [i["ref"] for i in r.structured_content["items"]] == [bound]

        for tool in ("get_patient", "list_conditions", "list_medications", "list_observations"):
            r = await c.call_tool(tool, {"patient_ref": bound})
            assert r.is_error is False, r.content
            _assert_no_leak(seeded, r)

    assert scope.events["dropped"] == 0
    assert set(methods) == {"GET"}


@pytest.fixture
async def bound_stdio_client(seeded: Seeded) -> AsyncIterator[Client]:
    uv = shutil.which("uv")
    assert uv, "uv must be on PATH"
    params = StdioServerParameters(
        command=uv,
        args=["run", "--project", str(REPO), "fhir-over-mcp", "serve", "--patient", seeded.bound],
        env={"FHIR_BASE_URL": seeded.base, "FHIR_ALLOWLIST": seeded.base},
        cwd=str(REPO),
    )
    async with Client(params) as c:
        yield c


async def test_the_patient_flag_binds_the_server_over_stdio(
    bound_stdio_client: Client, seeded: Seeded
) -> None:
    mine = await bound_stdio_client.call_tool("get_patient", {"patient_ref": seeded.bound})
    assert mine.is_error is False
    theirs = await bound_stdio_client.call_tool("get_patient", {"patient_ref": seeded.patient})
    assert theirs.is_error is True
    _assert_no_leak(seeded, theirs)
