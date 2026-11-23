# 4. Python stack: the official MCP SDK, httpx, pydantic, uv

Date: 2026-11-22
Status: Accepted

## Context
The project needs an MCP server, an MCP client (for tests and the eval harness), a FHIR HTTP client, input and output validation, and an agent harness on the Claude API. Python is the default for Dan's projects where it fits, and Token Keeper set the conventions (uv, ruff, mypy strict, pytest).

## Options considered
1. **Python with the official MCP Python SDK** for both server and client, httpx for FHIR, pydantic for tool schemas and results, the Anthropic Python SDK for the agent.
2. **TypeScript with the official MCP TypeScript SDK.** Equally well supported for MCP, but Holdfast already covers TypeScript, and Python fits the eval and data work better.
3. **Python with a FHIR model library** (typed classes for every resource) instead of plain pydantic models for what the tools return. Stronger typing of raw resources, but a large dependency for the few fields the tools read, and the tools' own result models are what matter.

## Decision
Option 1. Python 3.12, uv, ruff, mypy (strict), pytest, Testcontainers for HAPI. The official MCP Python SDK for the server (stdio and Streamable HTTP) and the client. httpx for FHIR, reading raw JSON into small pydantic models for the fields each tool uses. The Anthropic Python SDK for the agent and judge (ADR 0006).

## Why
It's the language the project should prove, it matches Token Keeper's tooling so nothing new has to be learned for the toolchain, and using the official SDK on both sides means the protocol tests exercise the same code a real client would. Plain pydantic models keep the FHIR mapping explicit and testable.

## Consequences
- One language across server, harness, scorers and scripts.
- The FHIR mapping is ours to get right (partial dates, units, component observations); unit tests cover each shape the cohort contains.
- SDK APIs move; versions are pinned in `uv.lock`, and current MCP SDK usage is checked against its docs at slice 2, not taken from memory.
- If the FHIR mapping grows a lot, revisit option 3 with a new ADR.
