"""Load the committed cohort bundles into a local FHIR server.

This is the only code in the repo that writes to a FHIR server. It is an admin step for local
development, tests and CI, kept apart from the read-only client on purpose:
- it has its own raw httpx client and is never imported by `fhir_over_mcp.fhir` or the server
  (a unit test checks this), so it can't widen what the server can do (invariant 1);
- it refuses any base URL whose host isn't the local machine, so it can't write to a sandbox
  or anything else on the allowlist.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class LoadError(Exception):
    pass


def require_local(base_url: str) -> str:
    host = (urlsplit(base_url).hostname or "").lower()
    if host not in LOCAL_HOSTS:
        raise LoadError(f"refusing to load into {base_url}: the loader only writes to localhost")
    return base_url.rstrip("/")


def load_order(bundle_dir: Path) -> list[Path]:
    """Hospital and practitioner bundles first: patient bundles reference them conditionally."""
    files = sorted(bundle_dir.glob("*.json"))
    shared = [
        f
        for f in files
        if f.name.endswith(("-hospitalInformation.json", "-practitionerInformation.json"))
    ]
    return shared + [f for f in files if f not in shared]


def load_cohort(
    base_url: str,
    bundle_dir: Path,
    *,
    timeout_seconds: float = 300.0,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """POST each transaction bundle to the server root. Returns the number of bundles loaded."""
    base = require_local(base_url)
    files = load_order(bundle_dir)
    if not files:
        raise LoadError(f"no bundles in {bundle_dir}")
    with httpx.Client(timeout=timeout_seconds, transport=transport) as http:
        for path in files:
            bundle = json.loads(path.read_text())
            response = http.post(
                base, json=bundle, headers={"Content-Type": "application/fhir+json"}
            )
            if response.status_code >= 300:
                raise LoadError(f"{path.name}: {response.status_code} {response.text[:500]}")
    return len(files)
