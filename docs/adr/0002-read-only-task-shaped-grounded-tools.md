# 2. Read-only, task-shaped tools that carry their FHIR references

Date: 2026-11-22
Status: Accepted

## Context
The MCP server is the only way the agent sees the record, so its tool surface decides what the agent can do wrong. The agent needs enough to answer questions about conditions, medications and observations. It must not be able to write, wander into other patients' data, or present a value without saying where it came from. The eval's citation check (ADR 0005) needs every fact to be traceable to a resource.

## Options considered
1. **One generic tool:** `fhir_search(resource_type, params)` returning raw resources. Maximum reach, least code. But the agent has to know FHIR search syntax, raw resources are large (context cost), scoping has to be enforced on arbitrary queries, and the tool surface is effectively the whole FHIR API.
2. **Task-shaped tools** (`list_conditions`, `list_medications`, `list_observations`, …), each scoped to a patient, returning compact items with the resource's reference and its values copied as-is. More code, and a question the tools don't cover can't be answered.
3. **Pre-computed summaries:** one `get_patient_summary` tool returning a digest. Cheapest for the agent, but the server is then interpreting the record, which is the part we want measured, and citations become vague.

## Decision
Option 2. Five read-only tools in v1 (spec §4), each patient-scoped, with pydantic-validated inputs. Every returned item carries `ref` (`Type/id`) and values copied from the resource (codes with system and display, quantities with units, dates as recorded). Nothing is computed or interpreted. Empty results and errors are distinct. The FHIR client underneath has no write methods.

## Why
A narrow surface is what makes the safety claims checkable: read-only is true by construction, scoping is enforceable on known inputs and outputs, and references on every item make grounding testable. Compact items also keep the agent's context small. The cost is coverage: a question outside the tools gets "I can't find that", which the eval treats as the right answer for absent data, and a new tool is a deliberate, reviewed change.

## Consequences
- The eval measures the agent's reading and reasoning, not the server's summaries.
- Tool descriptions are part of the system under test; changing one is an eval-affecting change and goes through a run before merging.
- Adding a resource type means a new tool, tests and cases, which keeps scope honest.
- Revisit if the case set keeps needing questions the tools can't express; a scoped, read-only generic search could be added beside them by a new ADR.
