# 7. Cohort composition: two seeded Synthea runs, generated in a pinned container

Date: 2026-11-22
Status: Superseded by [8](0008-cohort-moved-to-synthetic-cohort.md) (the cohort is now owned by synthetic-cohort)

## Context
ADR 0003 settled on a pinned Synthea cohort loaded into HAPI, and left its size and shape to slice 1 (spec §13, Q1). The cohort has to be big enough for varied cases with a fair share of asthma and COPD patients, and small enough to commit and load quickly. It also has to come out the same on every regeneration, because the ground truth is computed from it.

What we found when checking Synthea v4.0.0 (the latest tagged release, March 2026) before generating:
- The `synthetichealth/synthea` image on Docker Hub was last pushed in 2016. The current way to run Synthea is the release jar, which needs Java 17 or later.
- `asthma.json` produces asthma conditions and inhaler prescriptions (maintenance and rescue submodules), but **no lung-function observations**.
- `copd.json` produces COPD conditions, spirometry procedures, **FEV1/FVC observations** (LOINC 19926-5), and inhalers.
- With default settings, billing resources (Claim, ExplanationOfBenefit) and embedded documents make up most of each bundle.

## Options considered
1. **One general Synthea population.** Simplest, but only a few patients in a few dozen would have asthma or COPD, too few for the respiratory cases.
2. **Only respiratory patients** (Synthea's `-k` keep filter). Plenty of respiratory data, but no ordinary patients, so "the record doesn't show that" cases would have little to ask about.
3. **Two seeded runs, merged:** a small general population plus a respiratory-enriched one.
4. **Hand-written lung-function readings for asthma patients** now, so every respiratory patient has them.

How to run Synthea: the stale Docker image, a local JDK, or the release jar inside a pinned JRE container.

## Decision
Option 3, and not option 4 for now.
- **General run:** 15 patients, seed 1122. **Respiratory run:** 20 patients, seed 2211, keeping only patients with active asthma or COPD (`cohort/keep_respiratory.json`). Both use 2026-11-01 as the reference date and as the end of the simulation (`-r` and `-e`), Massachusetts, living patients only.
- `cohort/synthea.properties`: five years of history; leave out Claim, ExplanationOfBenefit, Provenance, ImagingStudy, Media, DocumentReference and SupplyDelivery; no clinical notes; no run metadata; file names taken from Synthea's seeded patient ids; **one generator thread**.
- Synthea v4.0.0's release jar runs in `eclipse-temurin:21.0.12.1_1-jre-noble`, pinned by digest. The jar's sha256 is checked before it's used.
- `cohort/manifest.json` records the generation settings and a hash of every bundle. A unit test fails if the committed files don't match it.

The result (`fhir-over-mcp cohort check`): 35 patients, 9 with asthma and 11 with COPD, 20 with inhaler prescriptions, and 11 with lung-function readings, all of them COPD patients. 39 files, about 24 MB, which load into HAPI in under a minute.

## Why
Two runs give both kinds of patient the case types need (spec §5) at a size that commits and loads easily. Running the release jar in a pinned container means no local Java and the same Java everywhere. The exclusions remove resource types no tool reads, and they make up most of each bundle's size. Making up readings for asthma patients now would put hand-written data into the ground truth before any case needs it. Lung-function cases can use the COPD patients, and asthma cases can use conditions and inhalers.

## Consequences
- Regenerating with the same jar, image, settings and seeds gives byte-identical bundles: slice 1 generated three times in a row and the manifests matched. That took two settings we didn't expect to need:
  - **The end date.** With only `-r`, Synthea simulates up to the wall clock: records had events after the reference date, and an encounter still open at the end was given the generation time as its end, so one bundle changed on every run.
  - **One thread.** With Synthea's default of one thread per core, a bundle occasionally came out differently from an otherwise identical run (one file in 39, not every time). That would also make the output depend on the machine. One thread takes about five minutes for the whole cohort, which is fine for something run rarely.
- Any change to the settings, seeds, keep filter or Synthea version changes the cohort, the case set and every score computed from them. It's a reviewed change: regenerate, update the manifest, rerun `cohort check`, and note it in the run report.
- Asthma patients have no lung-function readings. If slice 4's case set needs them (for example "latest peak flow for this asthma patient"), hand-written additions get their own ADR, as ADR 0003 says.
- Synthea transaction bundles POST patient resources, so HAPI assigns its own ids. Code that needs a particular patient finds them by their Synthea identifier, not by id.
- Revisit the size if the case set needs more variety; the size check in `tests/unit/test_cohort.py` stops the committed bundles growing past about 40 MB unnoticed.
