# FHIR over MCP

An MCP server that gives an AI agent a read-only, patient-scoped view of a FHIR record, where every result carries the reference it came from. An eval suite scores agents using it against ground truth computed from the data, and counts every claim the agent can't back up.

**Status:** planning. Design: [docs/spec.md](docs/spec.md) and [docs/adr/](docs/adr/). Synthetic patients only (Synthea and public sandboxes); no real health data.
