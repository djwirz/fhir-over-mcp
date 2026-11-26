"""HAPI FHIR for integration tests, loaded with the pinned cohort once per session.

`FHIR_BASE_URL` set (CI's service container, or `docker compose up`): use that server.
Otherwise start the same pinned image with Testcontainers.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator

import httpx
import pytest
from synthetic_cohort import bundle_dir

from fhir_over_mcp.cohort.load import load_cohort

HAPI_IMAGE = (
    "hapiproject/hapi:v8.12.0-2"
    "@sha256:0ff86aac4c7bd13a1ee226444a70263bb46ed1c28ef543c30a44f865b80ca04e"
)


def _wait_for(base: str, timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/metadata", timeout=5).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(3)
    raise RuntimeError(f"HAPI at {base} did not come up in {timeout}s")


def _patient_count(base: str) -> int:
    r = httpx.get(f"{base}/Patient", params={"_summary": "count"}, timeout=30)
    r.raise_for_status()
    return int(r.json().get("total", 0))


@pytest.fixture(scope="session")
def fhir_base() -> Iterator[str]:
    if env_base := os.environ.get("FHIR_BASE_URL"):
        base = env_base.rstrip("/")
        _wait_for(base)
        if _patient_count(base) == 0:
            load_cohort(base, bundle_dir())
        yield base
        return

    if sys.platform == "darwin":
        # Docker Desktop on macOS can't bind-mount the user-level socket path into Testcontainers'
        # cleanup container ("operation not supported"); the VM-side path works.
        os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

    from testcontainers.core.container import DockerContainer

    container = DockerContainer(HAPI_IMAGE).with_env("hapi.fhir.fhir_version", "R4")
    container.with_exposed_ports(8080)
    with container:
        base = f"http://localhost:{container.get_exposed_port(8080)}/fhir"
        _wait_for(base)
        load_cohort(base, bundle_dir())
        yield base
