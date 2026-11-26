# FHIR over MCP

An MCP server that gives an AI agent a read-only, patient-scoped view of a FHIR record, where every result carries the reference it came from. An eval suite scores agents using it against ground truth computed from the data, and counts every claim the agent can't back up.

**Status:** slices 1 and 2 of 7 are merged (the MCP server runs over stdio); patient-context mode is next. Design: [docs/spec.md](docs/spec.md) and [docs/adr/](docs/adr/). Synthetic patients only (Synthea and public sandboxes); no real health data.

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
