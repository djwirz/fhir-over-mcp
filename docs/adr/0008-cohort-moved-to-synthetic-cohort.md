# 8. The cohort moved to synthetic-cohort, consumed pinned to a tag

Date: 2026-11-26
Status: Accepted

## Context
[ADR 0003](0003-pinned-synthea-cohort-in-local-hapi.md) committed the Synthea bundles to this repo, and [ADR 0007](0007-cohort-composition.md) settled what they hold and how they're generated. Coverage Check now needs synthetic members, ideally the same people. The cohort's data and tooling have moved to [synthetic-cohort](https://github.com/djwirz/synthetic-cohort), whose [ADR 0002](https://github.com/djwirz/synthetic-cohort/blob/main/docs/adr/0002-one-shared-cohort-repo.md) records why a shared repo was chosen over the alternatives. This ADR records this repo's side.

## Options considered
1. **Keep a copy here as well.** Nothing to change, but two owners of the same data, which can drift apart without anyone noticing, and the eval's ground truth would depend on which copy it read.
2. **Consume synthetic-cohort as a uv git dependency on a tag, and delete the copy here.** One owner. The tag and the commit id in `uv.lock` fix the bytes. The bundles are no longer visible in this tree.

## Decision
Option 2, starting at `v0.1.0`.
- The bundles come from `synthetic_cohort.bundle_dir()`. For the loader, the tests and the eval's ground truth, "the pinned cohort" and ADR 0005's "committed cohort bundles" mean the files of the pinned version.
- This repo keeps the loader (`cohort load`), which is HAPI-specific and its only write path. `cohort generate` and `cohort check` are gone; synthetic-cohort has its own `generate` and `check`.
- What the cohort holds and how it's generated is decided in synthetic-cohort. This supersedes ADR 0007 here, and the part of ADR 0003 that commits the bundles to this repo. The rest of ADR 0003 stands: a pinned cohort, loaded into HAPI in Docker, with ground truth read from the files.
- The server and the FHIR client don't import `synthetic_cohort` or the loader (a unit test checks this).

At the move, all 39 bundles in `v0.1.0` had the same sha256 and size as this repo's manifest.

## Why
The same people can then appear in both projects, with one history for the data. Independence of ground truth (invariant 6) is unchanged: answers are still computed from the bundle files by code, never through the server.

## Consequences
- The cohort changes only when this repo moves to a new tag. That's a reviewed change: it can change the case set and every score, so it's noted in the run report.
- The tests that hold the bundles to their manifest, and that the cohort still has the asthma, COPD, inhaler and lung-function data the eval needs, now run in synthetic-cohort, not here. A new tag that dropped something would show up here only through the tests and cases that use it.
- Installing needs access to GitHub and downloads the cohort with the package. To look at a bundle, look in the installed package.
- It's a runtime dependency because `cohort load` is part of the command line. The deployed server has no use for it, so slice 6 should decide whether to leave it out of the deployment.
- The facts about loading in ADR 0007 still apply: HAPI assigns its own ids, so code finds a patient by their Synthea identifier.
