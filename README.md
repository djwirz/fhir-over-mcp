# FHIR over MCP

An MCP server that gives an AI agent a read-only, patient-scoped view of a FHIR record, where every result carries the reference it came from. An eval suite scores agents using it against ground truth computed from the data, and counts every claim the agent can't back up.

**Status:** slices 1 to 3 of 7 are done (the MCP server runs over stdio, with patient-context mode); the eval without a model is next. Design: [docs/spec.md](docs/spec.md) and [docs/adr/](docs/adr/). Synthetic patients only (Synthea and public sandboxes); no real health data.

## Try it with Claude Code

You need Docker and [uv](https://docs.astral.sh/uv/).

```bash
docker compose up -d
```

```bash
uv run fhir-over-mcp cohort load
```

The patients come from [synthetic-cohort](https://github.com/djwirz/synthetic-cohort), installed by uv at a pinned tag ([ADR 0008](docs/adr/0008-cohort-moved-to-synthetic-cohort.md)). To see what the cohort contains:

```bash
uv run synthetic-cohort check
```

HAPI keeps its data in memory. To reset it, run `docker compose down`, then `up` and load again.

Then open this folder in Claude Code. It finds `.mcp.json` and asks whether to enable the `fhir-over-mcp` server. Approve it, then ask something like "Which patients have COPD, and what was the most recent FEV1/FVC for one of them?" Every item the tools return carries the FHIR reference it came from, such as `Observation/123`.

The server is read-only and talks only to FHIR servers on its allowlist (`config/default.toml`). The five tools are described in [docs/spec.md](docs/spec.md) §4.

## Patient-context mode

To bind the server to one patient, add `--patient` with a ref that `search_patients` returned:

```bash
uv run fhir-over-mcp serve --patient Patient/123
```

In Claude Code, add `"--patient", "Patient/123"` to `args` in `.mcp.json` and restart the server. HAPI assigns new ids each time the cohort is loaded, so look the ref up again after a reset.

A bound server refuses requests for any other patient, and `search_patients` returns the bound patient or nothing. It also checks what the FHIR server sends back: a record it can't confirm as this patient's is withheld, and the result says so. Refusals and withheld records are logged as `scope_guard` lines on stderr. The limits are in [ADR 0009](docs/adr/0009-patient-context-mode.md); in particular, free text inside the patient's own records is copied as recorded.
