"""The read-only FHIR client.

Invariant 1: this class can only send GET. It has `read` and `search`, both of which go through
`_get`; there is no method that sends anything else, and the tests audit the methods on the wire.

Invariant 2: the base URL must be on the allowlist when the client is built, and every URL it is
about to request (including paging links the server hands back) is checked again before sending.

Invariant 5: errors are raised (see `errors.py`); an empty list only ever means "no matches".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

from fhir_over_mcp.fhir.errors import (
    FhirHttpError,
    FhirUnavailable,
    NotAllowlisted,
    NotFound,
)

Resource = dict[str, Any]
# A search parameter may repeat (e.g. date=ge2020-01-01&date=le2021-01-01).
Params = Mapping[str, str | Sequence[str]]

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _normalise(url: str) -> tuple[str, str, int, str]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise NotAllowlisted(url)
    port = parts.port or _DEFAULT_PORTS[scheme]
    return scheme, parts.hostname.lower(), port, parts.path.rstrip("/")


def is_allowed(url: str, allowlist: list[str]) -> bool:
    """True if `url` is at or under one of the allowlisted base URLs.

    Scheme, host and port must match exactly; the path must equal the base path or continue it
    after a `/` (so `/fhir` allows `/fhir/Patient` but not `/fhirx`).
    """
    try:
        scheme, host, port, path = _normalise(url)
    except NotAllowlisted:
        return False
    for base in allowlist:
        try:
            b_scheme, b_host, b_port, b_path = _normalise(base)
        except NotAllowlisted:
            continue
        if (scheme, host, port) != (b_scheme, b_host, b_port):
            continue
        if path == b_path or path.startswith(b_path + "/"):
            return True
    return False


@dataclass(frozen=True)
class SearchResult:
    """Resources from a search. `truncated` is True when the page cap stopped it early.

    `withheld` counts resources the patient-context output check removed (ADR 0009).
    """

    resources: list[Resource]
    truncated: bool
    total: int | None
    withheld: int = 0


class ReadOnlyFhirClient:
    """GET-only access to one allowlisted FHIR base URL."""

    def __init__(
        self,
        base_url: str,
        allowlist: list[str],
        *,
        max_pages: int = 5,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not is_allowed(base_url, allowlist):
            raise NotAllowlisted(base_url)
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self.base_url = base_url.rstrip("/")
        self._allowlist = list(allowlist)
        self._max_pages = max_pages
        self._http = httpx.Client(
            timeout=timeout_seconds,
            headers={"Accept": "application/fhir+json", "User-Agent": "fhir-over-mcp"},
            follow_redirects=False,  # a redirect could leave the allowlist; treat it as an error
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def read(self, resource_type: str, resource_id: str) -> Resource:
        """Read one resource by type and id. Raises `NotFound` if it doesn't exist."""
        return self._get(f"{self.base_url}/{resource_type}/{resource_id}")

    def search(
        self,
        resource_type: str,
        params: Params | None = None,
        *,
        max_pages: int | None = None,
    ) -> SearchResult:
        """Search, following `next` links up to the page cap."""
        cap = self._max_pages if max_pages is None else max_pages
        if cap < 1:
            raise ValueError("max_pages must be at least 1")
        bundle = self._get(f"{self.base_url}/{resource_type}", params=params)
        total = bundle.get("total")
        resources = _entries(bundle)
        pages = 1
        while (next_url := _next_link(bundle)) is not None:
            if pages >= cap:
                return SearchResult(resources, truncated=True, total=total)
            bundle = self._get(next_url)
            resources.extend(_entries(bundle))
            pages += 1
        return SearchResult(resources, truncated=False, total=total)

    def _get(self, url: str, params: Params | None = None) -> Resource:
        if not is_allowed(url, self._allowlist):
            raise NotAllowlisted(url)
        try:
            response = self._http.get(url, params=params)
        except httpx.TimeoutException as e:
            raise FhirUnavailable(f"timed out requesting {url}") from e
        except httpx.TransportError as e:
            raise FhirUnavailable(f"could not reach {url}: {e}") from e
        if response.status_code == 404:
            raise NotFound(404, url, _detail(response))
        if response.status_code != 200:
            raise FhirHttpError(response.status_code, url, _detail(response))
        try:
            body = response.json()
        except ValueError as e:
            raise FhirHttpError(response.status_code, url, "response was not JSON") from e
        if not isinstance(body, dict):
            raise FhirHttpError(response.status_code, url, "response was not a FHIR resource")
        return body


def _entries(bundle: Resource) -> list[Resource]:
    if bundle.get("resourceType") != "Bundle":
        raise FhirHttpError(200, "", f"expected a Bundle, got {bundle.get('resourceType')!r}")
    out: list[Resource] = []
    for entry in bundle.get("entry", []):
        # Search bundles can carry OperationOutcome entries (mode "outcome"); those aren't data.
        if entry.get("search", {}).get("mode") == "outcome":
            continue
        if "resource" in entry:
            out.append(entry["resource"])
    return out


def _next_link(bundle: Resource) -> str | None:
    for link in bundle.get("link", []):
        if link.get("relation") == "next" and link.get("url"):
            return str(link["url"])
    return None


def _detail(response: httpx.Response) -> str:
    """A short reason: the OperationOutcome's diagnostics if there is one, else the status text."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase
    if isinstance(body, dict) and body.get("resourceType") == "OperationOutcome":
        issues = body.get("issue", [])
        texts = [i.get("diagnostics") or i.get("details", {}).get("text", "") for i in issues]
        joined = "; ".join(t for t in texts if t)
        if joined:
            return joined[:500]
    return response.reason_phrase
