"""Patient-context mode: one choke point between the tools and FHIR (invariant 3, ADR 0009).

A server bound to a patient hands its tools a `ScopedReader` instead of the FHIR client. It guards
requests on the way in and checks every resource on the way out, so tool code never holds another
patient's resource and can't put one in an item, a note or an error. It fails closed: a resource
type it has no rule for is refused.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal, Protocol

from mcp.server.mcpserver.exceptions import ToolError

from fhir_over_mcp.fhir import (
    FhirError,
    FhirHttpError,
    FhirUnavailable,
    NotAllowlisted,
    NotFound,
    ReadOnlyFhirClient,
    SearchResult,
)
from fhir_over_mcp.fhir.client import Params, Resource

log = logging.getLogger("fhir_over_mcp.scope_guard")

# FHIR ids, as in `tools.PatientRef`.
_PATIENT_REF = re.compile(r"Patient/([A-Za-z0-9\-.]{1,64})")
# Searches the output check has a rule for: Patient by its id, the rest by `subject`.
_BY_SUBJECT = frozenset({"Condition", "MedicationRequest", "Observation"})
_HIDDEN = "details are withheld in patient-context mode; see the server log"

EventKind = Literal["refused", "dropped"]


class Reader(Protocol):
    """What the tools need from FHIR: the client itself, or the scoped wrapper around it."""

    def read(self, resource_type: str, resource_id: str) -> Resource: ...

    def search(
        self, resource_type: str, params: Params | None = None, *, max_pages: int | None = None
    ) -> SearchResult: ...


class PatientScope:
    """The one patient a server process is bound to, and its guard-event count."""

    def __init__(self, patient_ref: str) -> None:
        match = _PATIENT_REF.fullmatch(patient_ref)
        # "." and ".." fit the id grammar but would be collapsed as path segments in a URL.
        if not match or match.group(1) in (".", ".."):
            raise ValueError(f"not a patient reference like Patient/<id>: {patient_ref!r}")
        self.patient_id = match.group(1)
        self.ref = patient_ref
        self.events: Counter[EventKind] = Counter()

    def record(self, kind: EventKind, what: str, refs: list[str]) -> None:
        """Count and log a guard event. Refs go to the log only, never into a tool result."""
        self.events[kind] += len(refs)
        event = {"kind": kind, "bound": self.ref, "what": what, "count": len(refs), "refs": refs}
        log.warning("scope_guard %s", json.dumps(event))

    def refuse(self, what: str, requested: str) -> ToolError:
        # The same words whether or not the requested record exists.
        self.record("refused", what, [requested])
        return ToolError(f"Refused: this server is bound to {self.ref} and reads no one else.")


@contextmanager
def _without_server_text() -> Iterator[None]:
    """A FHIR server's words (diagnostics, paging links) could name anyone: log, don't pass on."""
    try:
        yield
    except FhirError as e:
        log.warning("FHIR error in patient-context mode: %s", e)
        if isinstance(e, NotFound):
            raise NotFound(404, "", _HIDDEN) from None
        if isinstance(e, FhirHttpError):
            raise FhirHttpError(e.status, "", _HIDDEN) from None
        if isinstance(e, NotAllowlisted):
            raise NotAllowlisted("a link the FHIR server sent") from None
        if isinstance(e, FhirUnavailable):
            raise FhirUnavailable(_HIDDEN) from None
        raise FhirError(_HIDDEN) from None


def _label(resource: Resource) -> str:
    return f"{resource.get('resourceType')}/{resource.get('id')}"


class ScopedReader:
    """The FHIR client as seen by a bound server."""

    def __init__(self, client: ReadOnlyFhirClient, scope: PatientScope) -> None:
        self._client = client
        self.scope = scope
        # Exactly these: relative, or absolute on this server. Versioned, other-server and
        # non-patient references don't count.
        self._subjects = frozenset({scope.ref, f"{client.base_url}/{scope.ref}"})

    def read(self, resource_type: str, resource_id: str) -> Resource:
        """A read of the bound Patient, or of a Medication.

        A Medication has no patient subject, so it's allowed on purpose: no tool takes its id,
        and the only ids followed come from medication requests that passed the check.
        """
        requested = f"{resource_type}/{resource_id}"
        if resource_type == "Patient":
            if resource_id != self.scope.patient_id:
                raise self.scope.refuse("read", requested)
        elif resource_type != "Medication":
            raise self.scope.refuse("read", requested)
        with _without_server_text():
            resource = self._client.read(resource_type, resource_id)
        if _label(resource) != requested:
            self.scope.record("dropped", f"read {resource_type}", [_label(resource)])
            raise ToolError(
                "Couldn't get the data: the FHIR server answered with a different resource, "
                "which was withheld."
            )
        return resource

    def search(
        self, resource_type: str, params: Params | None = None, *, max_pages: int | None = None
    ) -> SearchResult:
        """Search within the bound patient, then drop whatever isn't theirs anyway."""
        scoped: dict[str, Any] = dict(params or {})
        if resource_type == "Patient":
            scoped["_id"] = self.scope.patient_id
        elif resource_type in _BY_SUBJECT:
            asked = scoped.get("patient", self.scope.patient_id)
            if asked != self.scope.patient_id:
                raise self.scope.refuse("search", f"Patient/{asked}")
            scoped["patient"] = self.scope.patient_id
        else:
            raise self.scope.refuse("search", resource_type)
        with _without_server_text():
            result = self._client.search(resource_type, scoped, max_pages=max_pages)

        kept: list[Resource] = []
        dropped: list[str] = []
        for r in result.resources:
            if r.get("resourceType") == "OperationOutcome":
                continue  # a message about the search, not a record; never passed on
            if self._is_theirs(resource_type, r):
                kept.append(r)
            else:
                dropped.append(_label(r))
        if dropped:
            self.scope.record("dropped", f"search {resource_type}", dropped)
            if not kept:
                # Not an empty result: we don't know that the record has none (invariant 5).
                raise ToolError(
                    "Couldn't get the data: nothing the FHIR server returned could be confirmed "
                    f"as {self.scope.ref}'s, so all of it was withheld."
                )
        # `total` counts what the server matched, which may include what was dropped.
        return SearchResult(kept, truncated=result.truncated, total=None, withheld=len(dropped))

    def _is_theirs(self, resource_type: str, r: Resource) -> bool:
        if r.get("resourceType") != resource_type:
            return False
        if resource_type == "Patient":
            return r.get("id") == self.scope.patient_id
        subject = r.get("subject")
        return isinstance(subject, dict) and subject.get("reference") in self._subjects
