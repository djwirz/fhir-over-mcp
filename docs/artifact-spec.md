# Artifact spec: FHIR over MCP

Filled from the kit's `templates/artifact-spec.md` before any agent builds code. Draft for PR #1, 2026-11-22.

## One line
An MCP server that lets an AI agent read a synthetic patient's record (conditions, medications, observations) from a FHIR server, plus an eval suite that checks every answer against the record and catches the agent when it makes something up.

## What it exercises
**Python, MCP (server and client), FHIR R4, the Claude API with tool use, agents, evals**, plus testing, CI and observability.
This list is a result of the design, not a target for it. Every item is there because the problem needs it: an agent can only read a record safely through a narrow, read-only tool surface (MCP); the record lives in FHIR; and "the answers are right" is a claim only an eval with code-derived ground truth can back. No skills score gates any slice, and a tool is never added just to show it off (CLAUDE.md).

## Problem and users
- **Who has the problem:** teams putting an LLM in front of clinical data: chart summaries, "what is this patient on", pre-visit prep. Also the engineers who must show those answers are grounded before anyone trusts them.
- **What they do today:** paste chunks of the record into a prompt, or give the agent a generic "query FHIR" tool and hope. Checking quality is mostly someone reading a few answers and saying they look fine.
- **Why it matters:** a confident wrong answer about a medication or a lung-function reading is worse than no answer. Without per-claim citations and a repeatable eval, nobody can say how often the agent is wrong, or whether a prompt or model change made it better or worse.

## Scope
- **Must have:**
  - A read-only MCP server with a small set of task-shaped tools over FHIR R4 (patients, conditions, medications, observations), each result carrying the FHIR reference it came from (ADR 0002).
  - A pinned synthetic cohort (Synthea, including asthma and COPD patients) loaded into a local FHIR server, the same data every run (ADR 0003).
  - A patient-context mode: the server bound to one patient refuses to return anyone else's data (the bridge to Respiratory view's SMART launch).
  - An eval suite: questions with ground truth computed by code from the cohort, an agent harness that answers through the MCP tools, deterministic scoring of answers and citations, and a judge only where code can't decide (ADR 0005).
  - A comparison of a few Claude models on the same suite: score, cost and latency side by side (ADR 0006).
  - Usable from Claude Code or Claude Desktop over stdio, and deployed over HTTP.
  - Tests and CI, traces from agent to tool to FHIR request.
- **Nice to have:**
  - Running the same suite against the public SMART Health IT sandbox.
  - A prompt or tool-description hill-climb, with the before and after in the README.
  - Encounters and allergies as further tools.
- **Non-goals (on purpose):**
  - Writing to FHIR. No create, update or delete, ever.
  - Real patient data, or any data that isn't synthetic.
  - Clinical advice or interpretation. Tools return what the record says; the agent's job is to report it with citations.
  - A user-facing app. That's Respiratory view, the next project, built on this one.
  - Full SMART on FHIR authorization here. Patient-context mode stands in for it; real launch and scopes belong to Respiratory view.
  - Fine-tuning or training models.

## Success metric
- **Grounding:** in the eval, no answer cites a resource the agent didn't retrieve in that run, and every cited value matches the resource. Unsupported claims are counted and shown, not hidden in an average.
- **Accuracy:** the suite's score for each model, with run-to-run spread, in the README. The number is whatever it turns out to be; the point is that it's measured honestly and reproducibly.
- **Scoping:** in patient-context mode, zero responses containing another patient's data, across the scoping tests and every eval run.
- **Cost:** a full eval run on the default model costs a few dollars at most; routine runs use a cheaper subset. Monthly LLM spend stays at a few dollars.

## Constraints
- **Time:** two to three focused weeks.
- **Budget:** free tiers first; a budget alarm on any paid account before deploying. A few dollars a month of Anthropic API use is fine.
- **Data:** Synthea-generated patients and public sandboxes only. The server refuses FHIR hosts that aren't on its allowlist.
- **Privacy:** no employer details. Any comparison to past work stays at the level of public patterns, with no employer names.
- **Token budget:** eval runs have a per-run cost cap and record token usage from the API on every call.

## Quality plan
- **Tests:**
  - Unit tests for mapping FHIR resources to tool results (references, units, dates) and for the ground-truth generator.
  - Integration tests against a real FHIR server (HAPI in Docker) loaded with the pinned cohort.
  - MCP protocol tests: a real MCP client session against the server over stdio and HTTP.
  - Scoping tests: a bound server asked every way we can think of for another patient's data.
- **Evals:**
  - Eval set: question templates over the cohort (single facts, lists, latest value, date ranges, and questions whose honest answer is "the record doesn't say").
  - Ground truth computed by code from the cohort files, never by a model; a hand-verified sample.
  - Pass criteria: per-question deterministic checks plus a citation check; a judge, calibrated against hand labels, only for free-text faithfulness.
  - Runs keep full trajectories, separate infra errors from wrong answers, and report spread across repeated runs. An oracle and a null baseline run through the whole pipeline before any paid run.
- **Observability:**
  - Structured logs with run, case and tool-call ids.
  - OpenTelemetry traces from the agent's model call through the MCP tool call to the FHIR HTTP request.
  - Metrics for tool latency, FHIR errors, refusals by the scoping guard, and eval score per run.
- **Accessibility and performance:** no UI in this project. Tool calls should feel instant next to the model's own time; measured from traces, not promised.
- **Security basics:**
  - Read-only FHIR client (no write methods exist), FHIR host allowlist.
  - Secrets (Anthropic key, any deploy credentials) in the host's or CI's secret store.
  - Every tool input validated with pydantic.
  - The remote server isn't open to the world without some access control (spec §13).
  - gitleaks in CI.
- **CI/CD:** every PR runs ruff, mypy, unit, integration and protocol tests (HAPI as a service container), the eval harness's oracle and null runs (no API cost), and gitleaks. Paid eval runs are by hand or opt-in (spec §13). Merges to `main` deploy once there's a deploy.

## Depth target
Current rung: 0 · Target rung: 3 (engineered)

## Build stack (in order)
1. Python 3.12 with uv, ruff, mypy (strict), pytest (ADR 0004).
2. The official MCP Python SDK for the server and the test client; httpx for FHIR; pydantic for tool inputs and results.
3. HAPI FHIR in Docker locally and in CI, loaded with a pinned Synthea cohort (ADR 0003).
4. The Anthropic Python SDK for the agent harness and the judge (ADR 0006).
5. OpenTelemetry, with traces exported to a free hosted backend.
6. Hosting for the remote MCP server: settled at the deploy slice (spec §13).
