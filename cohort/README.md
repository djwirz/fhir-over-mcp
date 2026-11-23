# The pinned cohort

Synthetic patients from Synthea v4.0.0, committed as FHIR R4 transaction bundles in `bundles/`. They are the single source for both the FHIR server's data and the eval's ground truth. Why it's shaped this way: [ADR 0003](../docs/adr/0003-pinned-synthea-cohort-in-local-hapi.md) and [ADR 0007](../docs/adr/0007-cohort-composition.md).

| File | What it is |
|---|---|
| `bundles/` | `general-*` and `resp-*` patient bundles, plus each run's hospital and practitioner bundles |
| `manifest.json` | How the bundles were made, and the sha256 of each one. A unit test checks them. |
| `synthea.properties` | Synthea settings that differ from its defaults |
| `keep_respiratory.json` | The keep filter for the respiratory run: active asthma or COPD |

## Load it into local HAPI

```bash
docker compose up -d
```

```bash
uv run fhir-over-mcp cohort load
```

HAPI keeps its data in memory. To reset it, run `docker compose down` and then `up` again.

## See what it contains

```bash
uv run fhir-over-mcp cohort check
```

## Regenerate it (only to change the cohort)

```bash
uv run fhir-over-mcp cohort generate
```

This downloads the Synthea jar once to `.cache/` and checks its sha256, runs Synthea in a pinned JRE container (so you don't need Java installed), replaces `bundles/` and rewrites `manifest.json`. A changed cohort changes the case set and every score, so it's a reviewed change (ADR 0007).
