"""The server over real stdio, driven by the official SDK client (spec §10 protocol tests).

This is the transport Claude Code and Claude Desktop use: the client launches
`fhir-over-mcp serve` as a subprocess and speaks MCP over its stdin and stdout.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator

import pytest
from mcp import Client, StdioServerParameters

from fhir_over_mcp.cohort import COHORT_DIR

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

REPO = COHORT_DIR.parent


@pytest.fixture
async def stdio_client(fhir_base: str) -> AsyncIterator[Client]:
    uv = shutil.which("uv")
    assert uv, "uv must be on PATH"
    # The child gets a minimal environment (SDK default), so the FHIR settings are passed in.
    params = StdioServerParameters(
        command=uv,
        args=["run", "--project", str(REPO), "fhir-over-mcp", "serve"],
        env={"FHIR_BASE_URL": fhir_base, "FHIR_ALLOWLIST": fhir_base},
        cwd=str(REPO),
    )
    async with Client(params) as c:
        yield c


async def test_lists_tools_with_schemas_over_stdio(stdio_client: Client) -> None:
    tools = {t.name: t for t in (await stdio_client.list_tools()).tools}
    assert set(tools) == {
        "search_patients",
        "get_patient",
        "list_conditions",
        "list_medications",
        "list_observations",
    }
    observations = tools["list_observations"]
    assert observations.input_schema["required"] == ["patient_ref"]
    assert "latest_only" in observations.input_schema["properties"]
    assert observations.output_schema is not None
    assert "items" in observations.output_schema["properties"]


async def test_calls_a_tool_and_gets_structured_items_over_stdio(stdio_client: Client) -> None:
    found = await stdio_client.call_tool(
        "search_patients", {"condition_code": "http://snomed.info/sct|87433001"}
    )
    assert found.is_error is False
    assert found.structured_content is not None
    assert found.structured_content["matched"] > 0
    ref = found.structured_content["items"][0]["ref"]
    assert ref.startswith("Patient/")

    latest = await stdio_client.call_tool(
        "list_observations",
        {"patient_ref": ref, "code": "http://loinc.org|19926-5", "latest_only": True},
    )
    assert latest.is_error is False
    assert latest.structured_content is not None
    [item] = latest.structured_content["items"]
    assert item["ref"].startswith("Observation/")
    assert item["value_quantity"]["unit"] == "%"


async def test_errors_arrive_as_tool_errors_over_stdio(stdio_client: Client) -> None:
    missing = await stdio_client.call_tool("get_patient", {"patient_ref": "Patient/does-not-exist"})
    assert missing.is_error is True
    assert "No Patient/does-not-exist" in missing.content[0].text  # type: ignore[union-attr]
