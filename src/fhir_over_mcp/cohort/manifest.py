"""The cohort manifest: how the bundles were made, and a hash of every file.

A unit test compares the committed bundles against it, so a cohort change can't slip in unnoticed
(it would silently change the ground truth; invariant 6, ADR 0003).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fhir_over_mcp.cohort import BUNDLE_DIR, MANIFEST


def file_hashes(bundle_dir: Path = BUNDLE_DIR) -> dict[str, dict[str, Any]]:
    return {
        p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
        for p in sorted(bundle_dir.glob("*.json"))
    }


def write_manifest(generation: dict[str, Any], path: Path = MANIFEST) -> None:
    files = file_hashes()
    body = {
        "generation": generation,
        "total_bytes": sum(f["bytes"] for f in files.values()),
        "files": files,
    }
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")


def read_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data
