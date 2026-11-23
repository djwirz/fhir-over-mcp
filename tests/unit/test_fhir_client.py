"""The read-only client: invariants 1 (GET only), 2 (allowlist) and 5 (errors aren't empties)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from fhir_over_mcp.fhir import (
    FhirHttpError,
    FhirUnavailable,
    NotAllowlisted,
    NotFound,
    ReadOnlyFhirClient,
)
from fhir_over_mcp.fhir.client import is_allowed

BASE = "http://fhir.test/fhir"
ALLOW = [BASE]

Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    """A mock transport that remembers every request it saw."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._handler(request)

        return httpx.MockTransport(handle)

    @property
    def methods(self) -> set[str]:
        return {r.method for r in self.requests}


def bundle(resources: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 99,
        "entry": [{"resource": r, "search": {"mode": "match"}} for r in resources],
        "link": [{"relation": "self", "url": BASE}],
    }
    if next_url:
        body["link"].append({"relation": "next", "url": next_url})
    return body


def paged(pages: int) -> Handler:
    """A server with `pages` pages of one Observation each, linked by `next`."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("_page", "1"))
        nxt = f"{BASE}?_getpages=x&_page={page + 1}" if page < pages else None
        return httpx.Response(
            200, json=bundle([{"resourceType": "Observation", "id": str(page)}], nxt)
        )

    return handler


def client(rec: Recorder, **kw: Any) -> ReadOnlyFhirClient:
    return ReadOnlyFhirClient(BASE, ALLOW, transport=rec.transport(), **kw)


# --- invariant 1: read-only -------------------------------------------------------------------


def test_client_has_no_write_methods() -> None:
    names = {n.lower() for n in dir(ReadOnlyFhirClient)}
    for verb in ("post", "put", "patch", "delete", "create", "update", "transaction", "batch"):
        assert not any(verb in n for n in names), verb


def test_only_get_on_the_wire_across_every_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/Patient/missing"):
            return httpx.Response(404, json={"resourceType": "OperationOutcome", "issue": []})
        if path.endswith("/Patient/boom"):
            return httpx.Response(500)
        if path.endswith("/Patient/p1"):
            return httpx.Response(200, json={"resourceType": "Patient", "id": "p1"})
        return paged(3)(request)

    rec = Recorder(handler)
    with client(rec, max_pages=2) as c:
        c.read("Patient", "p1")
        c.search("Observation", {"patient": "p1"})
        with pytest.raises(NotFound):
            c.read("Patient", "missing")
        with pytest.raises(FhirHttpError):
            c.read("Patient", "boom")
    assert len(rec.requests) >= 5
    assert rec.methods == {"GET"}


# --- invariant 2: allowlist --------------------------------------------------------------------


def test_refuses_to_start_off_the_allowlist() -> None:
    with pytest.raises(NotAllowlisted):
        ReadOnlyFhirClient("https://elsewhere.example/fhir", ALLOW)


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("http://fhir.test/fhir", True),
        ("http://fhir.test/fhir/", True),
        ("http://FHIR.test/fhir/Patient/1", True),
        ("http://fhir.test:80/fhir/Patient", True),
        ("http://fhir.test/fhirx/Patient", False),
        ("http://fhir.test/other", False),
        ("https://fhir.test/fhir", False),
        ("http://fhir.test:8080/fhir", False),
        ("http://fhir.test.evil.example/fhir", False),
        ("http://user@evil.example/fhir", False),
        ("file:///etc/passwd", False),
        ("not a url", False),
    ],
)
def test_allowlist_matching(url: str, allowed: bool) -> None:
    assert is_allowed(url, ALLOW) is allowed


def test_next_link_off_the_allowlist_is_refused_not_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=bundle([{"resourceType": "Observation", "id": "1"}], "https://evil.example/x")
        )

    rec = Recorder(handler)
    with client(rec) as c, pytest.raises(NotAllowlisted):
        c.search("Observation")
    assert [r.url.host for r in rec.requests] == ["fhir.test"]


def test_redirects_are_not_followed() -> None:
    rec = Recorder(lambda _: httpx.Response(302, headers={"Location": "https://evil.example/"}))
    with client(rec) as c, pytest.raises(FhirHttpError):
        c.read("Patient", "p1")
    assert len(rec.requests) == 1


# --- paging cap -----------------------------------------------------------------------------


def test_paging_cap_reports_truncation() -> None:
    rec = Recorder(paged(5))
    with client(rec, max_pages=2) as c:
        result = c.search("Observation")
    assert [r["id"] for r in result.resources] == ["1", "2"]
    assert result.truncated is True
    assert result.total == 99


def test_under_the_cap_is_not_truncated() -> None:
    rec = Recorder(paged(3))
    with client(rec, max_pages=5) as c:
        result = c.search("Observation")
    assert len(result.resources) == 3
    assert result.truncated is False


def test_outcome_entries_are_not_data() -> None:
    body = bundle([{"resourceType": "Observation", "id": "1"}])
    body["entry"].append(
        {"resource": {"resourceType": "OperationOutcome"}, "search": {"mode": "outcome"}}
    )
    rec = Recorder(lambda _: httpx.Response(200, json=body))
    with client(rec) as c:
        assert [r["resourceType"] for r in c.search("Observation").resources] == ["Observation"]


# --- invariant 5: honest empty ------------------------------------------------------------------


def test_empty_search_is_an_empty_list() -> None:
    rec = Recorder(lambda _: httpx.Response(200, json=bundle([])))
    with client(rec) as c:
        result = c.search("Condition", {"patient": "p1"})
    assert result.resources == []
    assert result.truncated is False


def test_unreachable_server_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with client(Recorder(handler)) as c, pytest.raises(FhirUnavailable):
        c.search("Condition")


def test_timeout_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with client(Recorder(handler)) as c, pytest.raises(FhirUnavailable):
        c.read("Patient", "p1")


def test_server_error_raises_with_outcome_text() -> None:
    outcome = {"resourceType": "OperationOutcome", "issue": [{"diagnostics": "bad param"}]}
    with (
        client(Recorder(lambda _: httpx.Response(400, json=outcome))) as c,
        pytest.raises(FhirHttpError) as err,
    ):
        c.search("Condition", {"nonsense": "1"})
    assert err.value.status == 400
    assert "bad param" in err.value.detail
    assert not isinstance(err.value, NotFound)


def test_non_json_body_raises() -> None:
    with (
        client(Recorder(lambda _: httpx.Response(200, text="<html>"))) as c,
        pytest.raises(FhirHttpError),
    ):
        c.read("Patient", "p1")


def test_search_that_returns_a_non_bundle_raises() -> None:
    body = json.dumps({"resourceType": "Patient", "id": "p1"})
    with (
        client(Recorder(lambda _: httpx.Response(200, text=body))) as c,
        pytest.raises(FhirHttpError),
    ):
        c.search("Patient")
