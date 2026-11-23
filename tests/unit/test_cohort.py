"""The committed cohort: unchanged since its manifest, and still has what the eval needs."""

from __future__ import annotations

from fhir_over_mcp.cohort.check import check
from fhir_over_mcp.cohort.manifest import file_hashes, read_manifest


def test_bundles_match_the_manifest() -> None:
    """A changed, added or missing bundle fails here: regenerate and review it on purpose."""
    assert file_hashes() == read_manifest()["files"]


def test_cohort_has_what_the_eval_needs() -> None:
    # Minimums, not exact counts, set a little under what the pinned cohort has (ADR 0007).
    report = check()
    assert len(report.patients) >= 30
    assert report.asthma >= 5
    assert report.copd >= 10
    assert report.with_inhalers >= 15
    assert report.with_lung_function >= 10
    # Size guard: the bundles are committed (spec §13 Q1).
    assert report.total_bytes < 40_000_000
