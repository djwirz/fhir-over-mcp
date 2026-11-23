"""Errors from the FHIR client.

"No matching data" is an empty search result. Everything here means "couldn't get the data",
and is raised, never returned as an empty list (spec §7, invariant 5).
"""


class FhirError(Exception):
    """Base class: the data could not be retrieved."""


class NotAllowlisted(FhirError):
    """A URL outside the allowlist was refused before any request was sent (invariant 2)."""

    def __init__(self, url: str) -> None:
        super().__init__(f"refused: {url} is not on the FHIR allowlist")
        self.url = url


class FhirUnavailable(FhirError):
    """The server could not be reached, or did not answer in time."""


class FhirHttpError(FhirError):
    """The server answered with an error status."""

    def __init__(self, status: int, url: str, detail: str) -> None:
        super().__init__(f"FHIR server returned {status} for {url}: {detail}")
        self.status = status
        self.url = url
        self.detail = detail


class NotFound(FhirHttpError):
    """A read by id found no such resource (404)."""
