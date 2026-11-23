# FHIR over MCP: spec

Status: Draft for PR #1 · Date: 2026-11-22 · Owner: Dan

## 1. Problem

LLM agents are being pointed at clinical records to answer questions like "what is this patient taking for their asthma" or "what was their last lung-function reading". Two things have to be true before anyone should trust those answers:
- **The agent can only see what it should.** Read-only, one patient at a time when it's acting for one patient, and only from a server it's allowed to talk to.
- **Every answer is grounded and measured.** Each claim points at the record entry it came from, and there's a repeatable number for how often the agent is right, wrong, or makes something up.

The usual failures:
- **Invented facts:** a medication or reading that isn't in the record, stated confidently.
- **Wrong details:** the right drug with the wrong dose, the right test with an old value, a unit dropped.
- **Silent gaps:** "no history of COPD" when the truth is "the tool call failed" or "the record doesn't cover that".
- **Leaking across patients:** a broad search tool returns someone else's data into the conversation.
- **Unmeasured changes:** a prompt or model swap makes things worse and nobody notices, because quality was judged by reading a few answers.

FHIR over MCP is a small MCP server that gives an agent a narrow, read-only, citation-carrying view of a FHIR record, and an eval suite that scores agents using it against ground truth computed from the data itself.

**What a viewer sees.**
1. Claude Code (or Claude Desktop) with the server connected, asked about a synthetic asthma patient. The answer lists controller and rescue medications and the latest lung-function reading, each with its FHIR reference.
2. The same question about a patient with no such data gets "the record doesn't show that", not a guess.
3. In patient-context mode, a request for another patient is refused by the server.
4. The eval report: the suite's score per model, with spread, cost and latency, and the unsupported-claim count.
5. One agent run's trace, from the model call through each tool call to the FHIR requests.

That sequence is the README GIF and the case study.

### Why this is worth building (portfolio framing)
It shows Python for a real service, the Model Context Protocol from both sides (a server, and a client driving it in tests and evals), FHIR R4 resources and search, an agent built on the Claude API with tool use, and an eval suite that holds up to scrutiny: code-derived ground truth, deterministic grading first, errors kept apart from wrong answers, spread across runs. It's also the data and evals layer for the next project, Respiratory view. The design draws only on public FHIR, SMART and MCP material.

## 2. Non-goals (v1)

- **Writes.** No FHIR create, update or delete. The FHIR client has no methods for them.
- **Real data.** Synthetic patients only. The server refuses FHIR base URLs not on its allowlist.
- **Clinical interpretation.** Tools return what the record says (values, units, dates, codes). Classifying a medication as "controller" or "rescue" is the agent's job, and the eval checks it against code lists, not clinical judgment.
- **A user-facing app.** That's Respiratory view.
- **SMART on FHIR launch and scopes.** Patient-context mode (§4) is the stand-in here. Real launch belongs to Respiratory view.
- **Every FHIR resource.** Patients, conditions, medications and observations in v1; others only if a question in the suite needs them.
- **Model training or fine-tuning.**

## 3. Appetite

- **Time:** two to three focused weeks. Three is the honest estimate, mostly because good eval cases take longer than code.
- **Money:** the Anthropic API at a few dollars a month; hosting on a free tier or close to it, with a budget alarm before the first deploy.
- **Shape:** seven thin PRs after this one, local-first. The first four never deploy (§12).
- **If time runs short, cut in this order.** The project exists to prove three things: the tools are read-only, scoped and grounded; the eval is trustworthy; and the agent's quality is measured, not asserted. Cut what proves none of those first:
  1. Running against the public SMART sandbox.
  2. The hill-climb.
  3. Extra resource types.
  4. More than two models in the comparison.

  **Never cut:**
  - The read-only client and the host allowlist.
  - Patient-context mode and its scoping tests.
  - References on every tool result.
  - Code-derived ground truth and the citation check.
  - The oracle and null-baseline runs.
  - Separating infra errors from wrong answers.
  - The trace.
  - The budget alarm.

## 4. Architecture

```
 eval harness / Claude Code / Claude Desktop                MCP server (Python)                   FHIR
 ───────────────────────────────────────────                ───────────────────                   ────
 agent (Claude API, tool use)                               tools (read-only, task-shaped)
   model call ──▶ tool_use                                    search_patients
   MCP client ── stdio or Streamable HTTP ────────────────▶   get_patient
                                                              list_conditions
                                                              list_medications
                                                              list_observations
                                                            scope guard (patient-context mode)
                                                            FHIR client (GET only, host allowlist) ──▶ HAPI FHIR (Docker)
                                                                                                       loaded with the
 eval: cases ◀── ground-truth generator ◀───────── pinned Synthea cohort (files) ──── loader ───────▶  pinned cohort
       scorers (deterministic, citation check, judge where needed)
       results.jsonl · errors.jsonl · trajectories
```

### Components
- **FHIR client.** httpx, GET only, against a base URL that must be on an allowlist (local HAPI, the SMART Health IT sandbox). Follows search paging up to a cap and says when it hit the cap.
- **Tools.** Task-shaped and patient-scoped (ADR 0002). Each takes validated inputs and returns a list of items; every item carries `ref` (`Condition/123`), the values exactly as the resource has them (codes with system and display, quantities with units, dates), and nothing computed. Empty results say so explicitly, and errors are errors, never an empty list.
- **Scope guard.** With `--patient Patient/<id>` (or the HTTP equivalent), the server is bound to that patient. Tools that take a patient reject any other id; `search_patients` returns only the bound patient. Every result is checked on the way out: any resource whose subject isn't the bound patient is dropped and counted as a guard event.
- **Transports.** stdio for local clients (Claude Code, Claude Desktop, the eval harness, tests); Streamable HTTP for the deployed server. The same tool code serves both.
- **Cohort and loader.** A Synthea run with a pinned seed and settings, small enough to commit as FHIR bundles (ADR 0003). A loader posts them into HAPI. The bundles are the single source for both the server's data and the eval's ground truth.
- **Ground-truth generator.** Reads the bundle files directly, not through the server or its tools, and produces each case's expected answer. That independence is the point: a bug in a tool can't also be a bug in the answer key.
- **Agent harness.** Runs one case: starts or connects to the server as an MCP client, offers its tools to Claude through the Messages API, runs the tool loop, and asks for a final answer in a fixed structure with claims and the refs behind each (ADR 0006).
- **Scorers.** Deterministic per case type; a citation check on every answer; a judge only for free-text faithfulness (ADR 0005).

### Tools (v1)
| Tool | Inputs | Returns |
|---|---|---|
| `search_patients` | name, birth date, condition code (any, optional) | patients: ref, name, birth date, sex |
| `get_patient` | patient ref | the patient's demographics |
| `list_conditions` | patient ref; optional clinical status, code | conditions: ref, code, clinical status, onset, abatement |
| `list_medications` | patient ref; optional status | medication requests: ref, medication code, status, authored date, dosage text |
| `list_observations` | patient ref; optional code, category, date range, `latest_only` | observations: ref, code, value with unit (or components), effective date |

Tool descriptions say what each tool doesn't do (no free-text search, no other patients) as plainly as what it does. They're part of the system under test, so changing them changes the eval.

## 5. The eval

### Cases
Each case is a question about one patient, a type, and an expected answer computed by the ground-truth generator. Types, each with its own scorer:
- **Fact:** a single value ("What is this patient's birth date?").
- **List:** a set ("Which active conditions does this patient have?"). Scored with precision and recall on codes.
- **Latest:** the most recent value of something ("What was the last recorded FEV1, and when?"). Value, unit and date must all match.
- **Window:** values in a date range.
- **Classify:** a set filtered by a code list ("Which of this patient's medications are inhaled corticosteroids?"). Code lists are pinned in the repo with their source.
- **Absent:** the record doesn't contain the answer. The right answer says so; any asserted value is wrong. These keep "always answer" from scoring well.
- **Out of scope** (patient-context mode only): a question about another patient. The right answer is a refusal, and the trajectory must show no other patient's data.

Questions name the patient by details a user would plausibly give, not by FHIR id, so the agent has to find the patient. Synthetic names and values mean the model can't answer from memory.

### Answers
The agent's final answer is structured: a short text answer, plus claims, each with a value and the refs it rests on. The harness asks for it through the API's structured output, so parsing never guesses.

### Scoring
1. **Citation check (every case).** Every ref in the answer must be one the agent's tool calls actually returned in this run, and the claim's value must match that resource. A failure is an unsupported claim, counted on its own.
2. **Deterministic check by type** against the expected answer.
3. **Judge (free text only).** Where a case wants prose (a short summary of respiratory history), a judge checks each sentence is supported by the retrieved resources. The judge is a different call with its own prompt, calibrated against hand labels on a sample before its numbers are trusted, and its cost is recorded separately.

Refusals by the model, truncated answers and infra failures (timeouts, FHIR down, API errors after retries) are their own outcomes, never folded into "wrong".

### Runs
- A run is a case set × a model × some repetitions. Every model call records its usage from the API, and every row records the model that actually answered.
- Output per run: `results.jsonl` (one row per case and repetition), `errors.jsonl` (attempts that never produced a scorable answer, with a failure class), and full trajectories (every message, tool call and result).
- A summary per run: score by case type with spread across repetitions, unsupported-claim count, refusals, errors, tokens, cost and latency.
- Before any paid run, an **oracle** (a scripted agent that answers from the ground truth, citing the right refs) must score near the top, and a **null baseline** (always "I don't know", or always the same answer) must score near the bottom. If either doesn't, the eval is broken.
- A per-run cost cap stops a run that's going over.

## 6. Interfaces
- **MCP:** the five tools in §4, over stdio (`fhir-over-mcp serve --fhir-base <url> [--patient Patient/<id>]`) and Streamable HTTP.
- **CLI:**
  - `cohort generate` (runs Synthea with the pinned settings; only needed to change the cohort),
  - `cohort load` (posts the committed bundles to a FHIR server),
  - `eval cases` (regenerates the case set from the bundles),
  - `eval run --model <id> --reps <n> [--subset <name>] [--max-cost <dollars>]`,
  - `eval oracle`, `eval null`, `eval report <run>`.
- **Config:** FHIR base URL and allowlist, models, run limits, through environment variables and a checked-in default file. No secrets in it.

## 7. Invariants and failure modes

The invariants (checked by the tests and by every eval run):
1. **Read-only:** no code path sends anything but GET to a FHIR server. The client has no write methods, and a test asserts the HTTP methods the server ever used.
2. **Allowlisted:** the server talks only to FHIR base URLs on its allowlist, and refuses to start otherwise.
3. **Scoped:** in patient-context mode, no tool result contains a resource whose subject is another patient.
4. **Grounded:** every item a tool returns carries the reference of the resource it came from, and its values are copied from that resource, not computed.
5. **Honest empty:** "no matching data" and "couldn't get the data" are different results. An error is never returned as an empty list.
6. **Independent ground truth:** expected answers come from the cohort files by code, never from a model and never through the server's tools.

| Failure | What happens |
|---|---|
| FHIR server down or slow | The tool returns an error result naming the problem. In an eval, the attempt goes to `errors.jsonl`, not scored as wrong. |
| Search returns more than the page cap | The tool returns what it has and says it was truncated, so the agent can narrow the search. |
| Agent asks for another patient in patient-context mode | The tool refuses with a clear message; the guard event is logged and counted. |
| A resource without a subject, or with an unexpected one, slips through a search | The output check drops it and counts a guard event. Integration tests seed one on purpose. |
| Model refuses | Recorded as a refusal, its own outcome in the report. |
| Answer cut off at `max_tokens` | Recorded as truncated, not scored as wrong. |
| API rate limits or errors | Retried with backoff; retries recorded on the row. After the cap, the attempt goes to `errors.jsonl`. |
| The answer cites a ref the agent never retrieved | An unsupported claim. The case fails the citation check whatever else is right. |
| A run is heading over its cost cap | It stops, and the report says it was cut short and where. |

## 8. Observability
- **Logs:** structured JSON with run id, case id, tool-call id, tool name, FHIR request URL (synthetic data, but no bodies at info level) and timing.
- **Traces:** OpenTelemetry. One trace per eval case (or per Claude Code conversation turn): the model calls, each tool call (client side and server side, linked by context passed over MCP where the transport allows), and each FHIR HTTP request. The trace screenshot is an acceptance criterion.
- **Metrics:**
  - Tool calls by tool and outcome, and their latency.
  - FHIR errors.
  - Scope-guard events (should be zero outside the scoping tests).
  - Per eval run: score, unsupported claims, refusals, errors, cost.
- **Alerts (deployed server):** FHIR errors above a small threshold for a few minutes; any scope-guard event outside tests; the budget alarm.

## 9. Security
- Read-only by construction; the allowlist checked at startup and on every request.
- The Anthropic API key lives in the CI secret store and a local `.env` (git-ignored), never in logs, trajectories or PR text. Trajectories are scrubbed of headers.
- Tool inputs validated with pydantic: ids must look like FHIR ids, dates must be dates, limits are capped.
- The remote server has access control before it's public (§13).
- gitleaks in CI; `.env.example` holds placeholders only.
- Tool results are data, and the agent's system prompt says so. A synthetic record with instruction-like text in a note is one of the eval cases.

## 10. Testing and CI
- **Unit:** mapping FHIR resources to tool items (refs, codes, quantities with units, partial dates), the scope guard, input validation, the ground-truth generator, and each scorer against hand-written right and wrong answers.
- **Integration:** HAPI FHIR in Docker (Testcontainers locally, a service container in CI), loaded with the pinned cohort. Each tool against known patients; the paging cap; a resource with a foreign subject seeded on purpose for the guard; the method audit for invariant 1.
- **Protocol:** an MCP client session from the official SDK against the server over stdio and over HTTP: tool listing, schemas, calls, errors.
- **Scoping:** the bound server asked for another patient through every tool and argument we can think of; zero leaks.
- **Eval harness:** oracle and null runs on every PR (no API calls). A replay test runs the scorers on recorded trajectories, so scoring changes are caught without spending money.
- **Paid evals:** by hand, or opt-in in CI (§13), on a subset with a cost cap.
- **CI on every PR:** ruff, mypy, unit, integration, protocol, scoping, oracle and null, gitleaks.

### Local development
`docker compose up` brings up HAPI FHIR; `uv run fhir-over-mcp cohort load` loads the cohort; `uv run fhir-over-mcp serve` runs the server over stdio. A `.mcp.json` example connects Claude Code to it. Paid eval runs need `ANTHROPIC_API_KEY` in `.env`.

## 11. Acceptance criteria (v1 "done", depth 3)
1. **Usable:** Claude Code connects to the server over stdio and answers questions about cohort patients with refs. The deployed server answers over HTTP. A GIF of this is in the README.
2. **Measured:** a full eval run for at least two models, with spread across repetitions, unsupported claims, refusals, errors, cost and latency, is in the README. The oracle and null results are shown beside it.
3. **Scoped:** the scoping tests pass, and no eval run has a guard event or a leak.
4. **Tests and CI:** every automated category in §10 runs on each PR. Deploys come only from `main`.
5. **Traced:** a trace screenshot of one eval case, model to tool to FHIR, is in the README.
6. **Ops:** the alerts in §8 exist and each has been fired once on purpose; `docs/runbook.md` covers each; the budget alarm existed before the first deploy.
7. **Record:** ADRs for every expensive decision, and each PR says how AI was used and what it got wrong.

Stretch: the SMART sandbox run, a hill-climb on tool descriptions or the system prompt, encounters and allergies.

## 12. Proposed slice plan (one PR each, after this spec)
1. **Local foundation:**
   - The uv project, ruff, mypy, pytest, CI with gitleaks.
   - Docker Compose for HAPI FHIR.
   - The cohort: Synthea settings and seed, a check that the asthma and COPD modules produce what the cases need (conditions, inhaler medications, lung-function observations), and the committed bundles.
   - The loader and the read-only FHIR client with the allowlist.
2. **The MCP server over stdio:** the five tools, grounded results, honest empties and errors, input validation, the method audit, and protocol tests. A `.mcp.json` example for Claude Code.
3. **Patient-context mode:** the scope guard, the output check, and the scoping tests (invariant 3).
4. **The eval without a model:** the ground-truth generator, the case set (every type in §5), the scorers, the citation check, `results.jsonl` / `errors.jsonl` / trajectories, and the oracle and null runs in CI. A hand-verified sample of cases.
5. **The agent and the first real runs:** the harness on the Claude API, structured answers, retries and cost caps, the judge and its calibration against hand labels, then the first runs for two models with repetitions. The README table.
6. **Deploy and observe:** budget alarm first, then Streamable HTTP, access control, the host, deploy on merge and a smoke test; OpenTelemetry traces across agent, server and FHIR; metrics, alerts (each fired once) and the runbook.
7. **Demo polish:** the README GIF, the trace screenshot, the eval chart, the SMART sandbox run if there's time, and case-study notes.

## 13. Open questions (settle before slice 1 unless noted)
1. **Cohort size and shape.** Big enough for varied cases and a fair share of asthma and COPD patients, small enough to commit and load fast. Recommendation: a few dozen patients, with Synthea's settings pushed toward respiratory conditions, and a check on bundle size before committing. Verify the asthma and COPD modules first (build-plan backlog). Decide in slice 1.
2. **Hosting the remote server.** What does the deployed server read from? Recommendation: the deployed server reads the public SMART Health IT sandbox (nothing to host but the server, and Respiratory view launches from the same place), while the eval runs against the pinned local cohort. Host options: AWS Lambda (Token Keeper already runs there) or a small container host. Check current prices and free limits, and whether the MCP SDK's HTTP transport runs stateless, at slice 6, and write the ADR then.
3. **Access control on the remote server.** The MCP spec has its own authorization flow (OAuth); a bearer token is simpler. Recommendation: a token or passcode for v1, and leave OAuth to Respiratory view where SMART scopes make it worth it. Decide by slice 6.
4. **Paid evals in CI.** Never, opt-in by label, or nightly on a small subset? Recommendation: opt-in by label, on a small subset on the cheapest model, with a hard cost cap; full runs by hand. Decide by slice 5.
5. **Which models to compare.** Recommendation: the current Opus as the default agent and at least one cheaper model (Sonnet or Haiku) beside it; the judge on a model that isn't the one being judged where that's practical. Confirm current model ids and prices at slice 5 (ADR 0006).
6. **Case-study angle.** How much comparison to past professional work is safe to make? Public patterns only, no employer names. Decide in slice 7.
