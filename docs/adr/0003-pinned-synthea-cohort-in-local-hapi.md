# 3. A pinned Synthea cohort, loaded into a local HAPI FHIR server

Date: 2026-11-22
Status: Accepted; superseded in part by [8](0008-cohort-moved-to-synthetic-cohort.md) (the bundles are no longer committed here)

## Context
The eval is only meaningful if the data is the same every run and the answer key can be computed from it. Data must be synthetic. The project also wants asthma and COPD patients, because Respiratory view builds on this one. Free sources: the SMART Health IT sandbox (hosted, sample patients), Epic's sandbox (registration, synthetic data), Synthea (generate our own), and HAPI FHIR (an open-source server that runs in Docker).

## Options considered
1. **Use the SMART Health IT sandbox directly.** Nothing to host, and it's where Respiratory view will launch. But the data isn't ours to pin: it can change, and its uptime isn't something CI should depend on.
2. **Generate a cohort with Synthea, commit the bundles, load them into HAPI in Docker** for development, tests, CI and evals. Fully reproducible and offline. Costs a Docker service and a one-time effort to tune Synthea toward respiratory conditions.
3. **Hand-written fixtures.** Tiny and exact, but not realistic enough to make the agent work for its answers, and slow to grow.

## Decision
Option 2 for everything that needs to be reproducible: a Synthea run with a pinned seed and settings, its bundles committed, a loader that posts them into HAPI FHIR running in Docker (locally through Docker Compose and Testcontainers, in CI as a service container). The SMART sandbox stays a second target: the deployed server can read it (spec §13), and running the suite against it is a stretch goal.

## Why
Reproducibility is what lets two eval runs be compared, and the committed bundles are what the ground-truth generator reads, independently of the server (ADR 0005). Synthea gives realistic, varied records at no cost, and its synthetic names and values mean a model can't answer from memory. HAPI is a real FHIR server, so search, paging and references behave as they would elsewhere.

## Consequences
- The bundles live in the repo; their size has to stay reasonable (spec §13, Q1).
- Changing the cohort changes the case set and invalidates comparisons with earlier runs; that's a reviewed change with a note in the run report.
- HAPI is a JVM service; CI and local runs need Docker and a little patience on startup.
- Whether Synthea's asthma and COPD modules produce inhaler medications and lung-function readings in usable shape is unverified. Slice 1 checks it before the cohort is fixed; if they don't, small hand-written additions to the bundles are the fallback, recorded in a new ADR.
