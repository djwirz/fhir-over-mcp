"""Read resources straight from the pinned cohort's bundles: the independent side of every check."""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import cache
from typing import Any

from synthetic_cohort import bundle_dir

Resource = dict[str, Any]


@cache
def _bundles() -> tuple[Resource, ...]:
    return tuple(json.loads(p.read_text()) for p in sorted(bundle_dir().glob("*.json")))


def resources(resource_type: str) -> Iterator[Resource]:
    for bundle in _bundles():
        for entry in bundle.get("entry", []):
            r = entry.get("resource", {})
            if r.get("resourceType") == resource_type:
                yield r


def patient_bundles() -> Iterator[Resource]:
    for bundle in _bundles():
        if sum(e["resource"]["resourceType"] == "Patient" for e in bundle.get("entry", [])) == 1:
            yield bundle


def by_full_url(bundle: Resource, full_url: str) -> Resource:
    for e in bundle["entry"]:
        if e.get("fullUrl") == full_url:
            r: Resource = e["resource"]
            return r
    raise KeyError(full_url)
