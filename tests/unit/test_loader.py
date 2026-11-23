"""The loader is the repo's only write path: local-only, and unreachable from the read-only side."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest

import fhir_over_mcp
from fhir_over_mcp.cohort.load import LoadError, load_cohort, load_order, require_local

PACKAGE = Path(fhir_over_mcp.__file__).parent


@pytest.mark.parametrize(
    "url", ["https://r4.smarthealthit.org", "http://fhir.example/fhir", "http://10.0.0.5:8080/fhir"]
)
def test_refuses_non_local_targets(url: str) -> None:
    with pytest.raises(LoadError):
        require_local(url)


def test_accepts_local_targets() -> None:
    assert require_local("http://localhost:8080/fhir/") == "http://localhost:8080/fhir"
    assert require_local("http://127.0.0.1:1234/fhir") == "http://127.0.0.1:1234/fhir"


def test_shared_bundles_load_first(tmp_path: Path) -> None:
    for name in ("a-patient.json", "general-hospitalInformation.json",
                 "general-practitionerInformation.json", "z.json"):  # fmt: skip
        (tmp_path / name).write_text("{}")
    order = [p.name for p in load_order(tmp_path)]
    assert order[:2] == ["general-hospitalInformation.json", "general-practitionerInformation.json"]


def test_posts_each_bundle_and_stops_on_error(tmp_path: Path) -> None:
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(json.dumps({"resourceType": "Bundle", "type": "transaction"}))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(400 if len(seen) == 2 else 200, json={})

    with pytest.raises(LoadError, match=r"b\.json"):
        load_cohort("http://localhost:8080/fhir", tmp_path, transport=httpx.MockTransport(handler))
    assert seen == ["POST", "POST"]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_read_only_package_never_imports_the_loader() -> None:
    for path in (PACKAGE / "fhir").rglob("*.py"):
        assert not any(m.startswith("fhir_over_mcp.cohort") for m in _imports(path)), path


def test_only_the_loader_sends_writes() -> None:
    """No module but the loader calls an httpx write method."""
    writes = {"post", "put", "patch", "delete"}
    for path in PACKAGE.rglob("*.py"):
        if path.name == "load.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and node.attr in writes:
                pytest.fail(f"{path}: .{node.attr}")
