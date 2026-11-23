# fhir-over-mcp: instructions for Claude

An MCP server that gives an AI agent a read-only, patient-scoped view of a synthetic FHIR record, with every result carrying the reference it came from, plus an eval suite that scores agents using it against ground truth computed from the data. It's the data and evals layer for the next project, Respiratory view. Use they/them for Dan.

## Status
Building. The spec and ADRs 0001–0006 were accepted in PR #1. Slice 1 (local foundation: project, CI, HAPI, the pinned cohort, the read-only FHIR client) is in progress. Open questions are in spec §13; settle each one by the slice it names. The judgment behind the project is in `docs/artifact-spec.md`.

## How we work
- Pull requests only; never push to `main`. Merged PRs are the project's record.
- Spec first, then one thin end-to-end slice per PR (spec §12). Start each slice in plan mode.
- **Problem first.** Every tool, feature and dependency must earn its place through the problem: an invariant, a failure mode in spec §7, or an acceptance criterion in §11. Each slice's plan says which one for anything new. Skills shown are a by-product: never add something to show a skill, and never cut something the invariants need to save time on polish.
- Every decision that would be expensive to reverse gets an ADR in `docs/adr/` (format: ADR 0001). Don't decide silently in code.
- ADRs are written as `Accepted` in the PR that adds them; merging is the acceptance. Never rewrite a merged ADR's decision: add a new ADR that supersedes it.
- When the spec and the code disagree, stop and ask; update the spec in the same PR if the decision changes.
- Tool descriptions and the agent's prompt are part of the system under test: a change to either needs an eval run before merging, with the before and after in the PR.
- Never spend money on paid eval runs without Dan's go-ahead; oracle, null and replay runs are free and run any time.
- Built with Claude Code in the open: the case study gets a section on how AI was used and where it was wrong, so note real mistakes in PR descriptions.

## Constraints
- Never employer code, data or names beyond what's public. Synthetic data only (Synthea, the SMART Health IT sandbox).
- Python (it's the default here).
- Free tiers first; a budget alarm on any paid account before anything is deployed. A few dollars a month of Anthropic API use is fine.
- Secrets never in the repo, logs, trajectories or PR text.
- Target depth: deployed, tests and CI, ADRs, observability, evals.
- Numbers in docs: honest estimates in words, never false precision.
- Check current MCP SDK and Claude API usage against their docs before writing code; don't rely on memory.

## Invariants (spec §7): never break these
1. **Read-only:** nothing but GET ever reaches a FHIR server; the client has no write methods.
2. **Allowlisted:** the server talks only to FHIR base URLs on its allowlist and refuses to start otherwise.
3. **Scoped:** in patient-context mode, no tool result contains another patient's data.
4. **Grounded:** every tool item carries its resource's reference, with values copied, not computed.
5. **Honest empty:** "no matching data" and "couldn't get the data" are different results; an error is never an empty list.
6. **Independent ground truth:** expected answers come from the cohort files by code, never from a model or through the server's tools.
