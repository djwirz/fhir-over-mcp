"""The pinned Synthea cohort: generate it, check it, and load it into a local FHIR server."""

from pathlib import Path

COHORT_DIR = Path(__file__).resolve().parents[3] / "cohort"
BUNDLE_DIR = COHORT_DIR / "bundles"
MANIFEST = COHORT_DIR / "manifest.json"
