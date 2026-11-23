"""Read-only FHIR access. Nothing in this package can write to a FHIR server (invariant 1)."""

from fhir_over_mcp.fhir.client import ReadOnlyFhirClient, SearchResult
from fhir_over_mcp.fhir.errors import (
    FhirError,
    FhirHttpError,
    FhirUnavailable,
    NotAllowlisted,
    NotFound,
)

__all__ = [
    "FhirError",
    "FhirHttpError",
    "FhirUnavailable",
    "NotAllowlisted",
    "NotFound",
    "ReadOnlyFhirClient",
    "SearchResult",
]
