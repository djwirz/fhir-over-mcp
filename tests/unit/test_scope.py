"""Patient-context mode (invariant 3): a bound server asked for another patient, every way we can.

The fake FHIR server here ignores every search filter and hands back everyone's records, which is
the worst a real one could do. Each leak check scans the whole tool result, errors included, for
anything that belongs to the other patient.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from fhir_over_mcp.cli import main
from fhir_over_mcp.fhir import ReadOnlyFhirClient
from fhir_over_mcp.server.app import build_server
from fhir_over_mcp.server.scope import PatientScope, ScopedReader
from tests.unit.test_server import BASE, SETTINGS, Fake, Handler, bundle, text

pytestmark = pytest.mark.anyio

Resource = dict[str, Any]

BOUND = "Patient/p1"
OTHER = "Patient/p2"
# Appears in every record, id and value that isn't the bound patient's.
MARK = "ZZFOREIGN"


def patient(pid: str, family: str) -> Resource:
    return {"resourceType": "Patient", "id": pid, "name": [{"family": family}]}


def condition(rid: str, subject: Any, note: str = "asthma") -> Resource:
    r: Resource = {"resourceType": "Condition", "id": rid, "code": {"text": note}}
    if subject is not None:
        r["subject"] = subject
    return r


def request(rid: str, subject: str, medication: str) -> Resource:
    return {
        "resourceType": "MedicationRequest",
        "id": rid,
        "subject": {"reference": subject},
        "medicationReference": {"reference": f"Medication/{medication}"},
    }


def observation(rid: str, subject: str, value: str) -> Resource:
    return {
        "resourceType": "Observation",
        "id": rid,
        "code": {"text": "reading"},
        "subject": {"reference": subject},
        "valueString": value,
    }


MINE = {"reference": BOUND}
THEIRS = {"reference": f"Patient/{MARK}"}

RECORDS: dict[str, list[Resource]] = {
    "Patient": [patient("p1", "Mine"), patient(MARK, f"{MARK}-name")],
    "Condition": [condition("c1", MINE), condition(f"{MARK}-c", THEIRS, MARK)],
    "MedicationRequest": [
        request("m1", BOUND, "med1"),
        request(f"{MARK}-m", f"Patient/{MARK}", f"{MARK}-med"),
    ],
    "Observation": [
        observation("o1", BOUND, "mine"),
        observation(f"{MARK}-o", f"Patient/{MARK}", MARK),
    ],
}


def leaky(records: dict[str, list[Resource]] = RECORDS) -> Handler:
    """Ignores every filter: each search returns every patient's resources of that type."""

    def handle(r: httpx.Request) -> httpx.Response:
        parts = r.url.path.removeprefix("/fhir/").split("/")
        if len(parts) == 1:
            return httpx.Response(200, json=bundle(records.get(parts[0], [])))
        kind, rid = parts
        if kind == "Medication":
            return httpx.Response(200, json={"resourceType": kind, "id": rid, "code": {}})
        return httpx.Response(200, json=patient(rid, "Mine" if rid == "p1" else MARK))

    return handle


async def call(
    fake: Fake, tool: str, args: dict[str, Any], scope: PatientScope | None = None
) -> Any:
    scope = scope or PatientScope(BOUND)
    server = build_server(SETTINGS, scope=scope, transport=fake.transport())
    async with Client(server) as c:
        return await c.call_tool(tool, args)


def everything(result: Any) -> str:
    return text(result) + json.dumps(result.structured_content)


PATIENT_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("get_patient", {}),
    ("list_conditions", {}),
    ("list_conditions", {"clinical_status": "active", "code": "195967001"}),
    ("list_medications", {}),
    ("list_medications", {"status": "active"}),
    ("list_observations", {}),
    ("list_observations", {"code": "x", "latest_only": True}),
    (
        "list_observations",
        {"category": "laboratory", "date_from": "2020-01-01", "date_to": "2030-01-01"},
    ),
]


# --- on the way in --------------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "extra"), PATIENT_TOOLS)
@pytest.mark.parametrize(
    "other",
    [
        OTHER,
        f"Patient/{MARK}",
        "Patient/p",  # a prefix of the bound id
        "Patient/p10",  # the bound id is a prefix of it
        "Patient/P1",  # ids are case sensitive
    ],
)
async def test_another_patient_is_refused_before_fhir_is_called(
    tool: str, extra: dict[str, Any], other: str
) -> None:
    fake = Fake(leaky())
    scope = PatientScope(BOUND)
    r = await call(fake, tool, {"patient_ref": other, **extra}, scope)
    assert r.is_error is True
    assert r.structured_content is None
    assert f"Refused: this server is bound to {BOUND}" in text(r)
    assert fake.requests == []
    assert scope.events == {"refused": 1}


@pytest.mark.parametrize(("tool", "extra"), PATIENT_TOOLS)
@pytest.mark.parametrize(
    "ref",
    [
        "Patient/p1\n",
        "Patient/p1 ",
        "Patient/p1,p2",
        "Patient/p1&patient=p2",
        "Patient/p1/_history/1",
        "Patient/p1/../p2",
        "Patient/p1?_id=p2",
        "Patient/p1%2Cp2",
        f"{BASE}/Patient/p1",
        "p2",
        "Patient/.",
        "Patient/..",
        ["Patient/p1", "Patient/p2"],
    ],
)
async def test_malformed_refs_never_reach_fhir(tool: str, extra: dict[str, Any], ref: Any) -> None:
    fake = Fake(leaky())
    r = await call(fake, tool, {"patient_ref": ref, **extra})
    assert r.is_error is True
    assert fake.requests == []


async def test_the_refusal_is_the_same_whether_or_not_the_patient_exists() -> None:
    def only_p2(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if r.url.path.endswith("p2") else 404, json={})

    real = await call(Fake(only_p2), "get_patient", {"patient_ref": "Patient/p2"})
    made_up = await call(Fake(only_p2), "get_patient", {"patient_ref": "Patient/p3"})
    assert text(real) == text(made_up)


# --- the searches the server sends ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search_patients", {"name": f"{MARK}-name"}),
        ("search_patients", {"name": "Mine,Other&_id=p2"}),
        ("search_patients", {"birth_date": "1970-01-01"}),
        ("search_patients", {"condition_code": "1,2&_id=p2"}),
        ("list_conditions", {"patient_ref": BOUND, "code": f"x&patient={MARK}"}),
        ("list_observations", {"patient_ref": BOUND, "code": f"x,y&subject={MARK}"}),
        # Arguments the tools don't declare.
        ("search_patients", {"name": "a", "_id": "p2", "patient": "p2"}),
        ("list_conditions", {"patient_ref": BOUND, "patient": "p2", "subject": OTHER}),
        ("list_medications", {"patient_ref": BOUND, "_include": "*", "patient_id": "p2"}),
        ("get_patient", {"patient_ref": BOUND, "id": "p2"}),
    ],
)
async def test_no_argument_widens_the_search(tool: str, args: dict[str, Any]) -> None:
    fake = Fake(leaky())
    r = await call(fake, tool, args)
    assert fake.requests  # otherwise the checks below prove nothing
    for req in fake.requests:
        params = req.url.params
        assert "subject" not in params
        assert "_include" not in params
        if req.url.path.endswith("/fhir/Patient"):
            assert params.get_list("_id") == ["p1"]
        elif req.url.path.endswith(("/Condition", "/MedicationRequest", "/Observation")):
            assert params.get_list("patient") == ["p1"]
    # The arguments themselves are echoed in notes; no record data may be.
    returned = json.dumps((r.structured_content or {}).get("items", []))
    assert MARK not in returned
    assert "p2" not in returned


# --- on the way out -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args", "kept"),
    [
        ("search_patients", {"name": "anyone"}, BOUND),
        ("list_conditions", {"patient_ref": BOUND}, "Condition/c1"),
        ("list_medications", {"patient_ref": BOUND}, "MedicationRequest/m1"),
        ("list_observations", {"patient_ref": BOUND}, "Observation/o1"),
    ],
)
async def test_foreign_resources_are_dropped_counted_and_logged(
    tool: str, args: dict[str, Any], kept: str, caplog: pytest.LogCaptureFixture
) -> None:
    fake = Fake(leaky())
    scope = PatientScope(BOUND)
    with caplog.at_level(logging.WARNING, logger="fhir_over_mcp.scope_guard"):
        r = await call(fake, tool, args, scope)
    assert r.is_error is False
    assert [i["ref"] for i in r.structured_content["items"]] == [kept]
    assert r.structured_content["matched"] == 1
    assert "1 more" in r.structured_content["note"]
    assert "incomplete" in r.structured_content["note"]
    assert MARK not in everything(r)
    assert scope.events == {"dropped": 1}
    [logged] = [m for m in caplog.messages if m.startswith("scope_guard ")]
    event = json.loads(logged.removeprefix("scope_guard "))
    assert event["kind"] == "dropped"
    assert event["count"] == 1
    assert MARK in event["refs"][0]  # the log says what; the result never does


async def test_a_foreign_medication_request_is_never_followed() -> None:
    fake = Fake(leaky())
    await call(fake, "list_medications", {"patient_ref": BOUND})
    assert [r.url.path for r in fake.requests] == [
        "/fhir/MedicationRequest",
        "/fhir/Medication/med1",
    ]


async def test_a_foreign_unfollowable_medication_reference_is_not_quoted() -> None:
    bad = request(f"{MARK}-m", f"Patient/{MARK}", "x")
    bad["medicationReference"] = {"reference": f"https://{MARK}.example/Medication/1"}
    records = {"MedicationRequest": [request("m1", BOUND, "med1"), bad]}
    r = await call(Fake(leaky(records)), "list_medications", {"patient_ref": BOUND})
    assert r.is_error is False
    assert MARK not in everything(r)


@pytest.mark.parametrize(
    "subject",
    [
        None,
        {},
        {"display": "p1"},
        {"identifier": {"value": "p1"}},
        {"reference": OTHER},
        {"reference": "Patient/p10"},
        {"reference": "Group/p1"},
        {"reference": "Patient/p1/_history/2"},
        {"reference": "https://elsewhere.example/fhir/Patient/p1"},
        {"reference": "urn:uuid:p1"},
        "Patient/p1",
        [{"reference": BOUND}],
    ],
)
async def test_a_subject_that_is_not_the_bound_patient_is_dropped(subject: Any) -> None:
    records = {"Condition": [condition("c1", MINE), condition(f"{MARK}-c", subject, MARK)]}
    scope = PatientScope(BOUND)
    r = await call(Fake(leaky(records)), "list_conditions", {"patient_ref": BOUND}, scope)
    assert [i["ref"] for i in r.structured_content["items"]] == ["Condition/c1"]
    assert MARK not in everything(r)
    assert scope.events == {"dropped": 1}


async def test_an_absolute_reference_on_this_server_is_the_same_patient() -> None:
    records = {"Condition": [condition("c1", {"reference": f"{BASE}/{BOUND}"})]}
    scope = PatientScope(BOUND)
    r = await call(Fake(leaky(records)), "list_conditions", {"patient_ref": BOUND}, scope)
    assert r.structured_content["matched"] == 1
    assert scope.events == {}


async def test_another_resource_type_in_a_search_is_dropped() -> None:
    stray = observation(f"{MARK}-o", BOUND, MARK)
    records = {"Condition": [condition("c1", MINE), stray]}
    scope = PatientScope(BOUND)
    r = await call(Fake(leaky(records)), "list_conditions", {"patient_ref": BOUND}, scope)
    assert MARK not in everything(r)
    assert scope.events == {"dropped": 1}


async def test_an_operation_outcome_is_not_a_leak() -> None:
    records = {"Condition": [{"resourceType": "OperationOutcome", "issue": []}]}
    scope = PatientScope(BOUND)
    r = await call(Fake(leaky(records)), "list_conditions", {"patient_ref": BOUND}, scope)
    assert r.is_error is False
    assert r.structured_content["matched"] == 0
    assert scope.events == {}


async def test_foreign_resources_on_a_later_page_are_dropped() -> None:
    def pages(r: httpx.Request) -> httpx.Response:
        if "page" not in r.url.params:
            return httpx.Response(200, json=bundle([condition("c1", MINE)], f"{BASE}?page=2"))
        return httpx.Response(200, json=bundle([condition(f"{MARK}-c", THEIRS, MARK)]))

    r = await call(Fake(pages), "list_conditions", {"patient_ref": BOUND})
    assert r.structured_content["matched"] == 1
    assert MARK not in everything(r)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search_patients", {"name": "anyone"}),
        ("list_conditions", {"patient_ref": BOUND}),
        ("list_medications", {"patient_ref": BOUND}),
        ("list_observations", {"patient_ref": BOUND}),
        ("list_observations", {"patient_ref": BOUND, "code": "x", "latest_only": True}),
    ],
)
async def test_all_withheld_is_an_error_not_an_empty_record(
    tool: str, args: dict[str, Any]
) -> None:
    """Invariant 5: we can't say the record has none when we couldn't confirm what came back."""
    records = {kind: found[1:] for kind, found in RECORDS.items()}
    scope = PatientScope(BOUND)
    r = await call(Fake(leaky(records)), tool, args, scope)
    assert r.is_error is True
    assert r.structured_content is None
    assert "all of it was withheld" in text(r)
    assert MARK not in everything(r)
    assert scope.events == {"dropped": 1}


@pytest.mark.parametrize(
    ("tool", "args", "answer"),
    [
        ("get_patient", {"patient_ref": BOUND}, patient(MARK, MARK)),
        ("get_patient", {"patient_ref": BOUND}, observation("p1", OTHER, MARK)),
        ("list_medications", {"patient_ref": BOUND}, patient(MARK, MARK)),
        ("list_medications", {"patient_ref": BOUND}, observation("med1", OTHER, MARK)),
    ],
)
async def test_a_read_answered_with_something_else_is_withheld(
    tool: str, args: dict[str, Any], answer: Resource
) -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/MedicationRequest"):
            return httpx.Response(200, json=bundle([request("m1", BOUND, "med1")]))
        return httpx.Response(200, json=answer)

    scope = PatientScope(BOUND)
    r = await call(Fake(handler), tool, args, scope)
    assert r.is_error is True
    assert "withheld" in text(r)
    assert MARK not in everything(r)
    assert scope.events == {"dropped": 1}


# --- what a FHIR server says in an error ----------------------------------------------------------


@pytest.mark.parametrize("status", [400, 404, 500])
@pytest.mark.parametrize(("tool", "extra"), PATIENT_TOOLS)
async def test_server_diagnostics_are_not_passed_on(
    tool: str, extra: dict[str, Any], status: int
) -> None:
    outcome = {"resourceType": "OperationOutcome", "issue": [{"diagnostics": f"see {MARK}"}]}
    fake = Fake(lambda _: httpx.Response(status, json=outcome))
    r = await call(fake, tool, {"patient_ref": BOUND, **extra})
    assert r.is_error is True
    assert MARK not in everything(r)


async def test_a_paging_link_off_the_allowlist_is_not_quoted() -> None:
    elsewhere = f"https://{MARK}.example/fhir?page=2"
    fake = Fake(lambda _: httpx.Response(200, json=bundle([condition("c1", MINE)], elsewhere)))
    r = await call(fake, "list_conditions", {"patient_ref": BOUND})
    assert r.is_error is True
    assert "allowlist" in text(r)
    assert MARK not in everything(r)


# --- search_patients and the notes ----------------------------------------------------------------


async def test_search_with_no_match_does_not_say_there_is_no_such_patient() -> None:
    fake = Fake(lambda _: httpx.Response(200, json=bundle([])))
    r = await call(fake, "search_patients", {"name": "Someone"})
    assert r.is_error is False
    assert r.structured_content["items"] == []
    assert r.structured_content["note"] == (
        f"This server is bound to {BOUND}, so only that patient was searched. "
        "They don't match name=Someone."
    )


# --- fail closed, and unbound is unchanged --------------------------------------------------------


def test_a_type_without_a_rule_is_refused() -> None:
    fake = Fake(leaky())
    with ReadOnlyFhirClient(BASE, [BASE], transport=fake.transport()) as client:
        reader = ScopedReader(client, PatientScope(BOUND))
        for attempt in (
            lambda: reader.search("Encounter", {"patient": "p1"}),
            lambda: reader.search("Medication"),
            lambda: reader.read("Condition", "c1"),
            lambda: reader.search("Condition", {"patient": "p2"}),
        ):
            with pytest.raises(ToolError, match="Refused"):
                attempt()
    assert fake.requests == []


async def test_unbound_sends_no_scope_filter_and_withholds_nothing() -> None:
    fake = Fake(leaky())
    async with Client(build_server(SETTINGS, transport=fake.transport())) as c:
        found = await c.call_tool("search_patients", {"name": "anyone"})
        listed = await c.call_tool("list_conditions", {"patient_ref": OTHER})
    assert found.structured_content is not None
    assert found.structured_content["matched"] == 2
    assert listed.structured_content is not None
    assert listed.structured_content["matched"] == 2
    assert "withheld" not in listed.structured_content["note"]
    assert "_id" not in fake.requests[0].url.params


async def test_binding_does_not_change_the_listing_or_instructions() -> None:
    """Both are part of the system under test (CLAUDE.md)."""
    seen = []
    for scope in (None, PatientScope(BOUND)):
        async with Client(build_server(SETTINGS, scope=scope)) as c:
            seen.append((c.instructions, (await c.list_tools()).tools))
    assert seen[0] == seen[1]


# --- the command line -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["", "p1", "Patient/", "Patient/.", "Patient/..", "Patient/p1\n", "Patient/a,b", "Group/g1"],
)
def test_serve_refuses_a_bad_patient_rather_than_starting_unbound(
    value: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FHIR_BASE_URL", raising=False)
    monkeypatch.delenv("FHIR_ALLOWLIST", raising=False)
    assert main(["serve", "--patient", value]) == 2
    assert "refusing to start: --patient" in capsys.readouterr().err
