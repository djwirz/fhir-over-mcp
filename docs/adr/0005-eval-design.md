# 5. Eval design: code-derived ground truth, deterministic scoring first

Date: 2026-11-22
Status: Accepted

## Context
The eval is the project's main claim: it says how often an agent using these tools is right, wrong or making things up. A broken eval produces confident numbers that point the wrong way, so its design needs to be as deliberate as the server's. The common traps: answer keys written by a model, a judge doing work code could do, "no answer" scored like a wrong answer, infra failures counted against the model, single runs with no spread, and questions a model can answer from memory.

## Options considered
1. **LLM judge on every answer** against a reference written by hand or by a model. Flexible, quick to start. But the score then depends on the judge, references written by a model reward imitating it, and every scoring run costs money.
2. **Code-derived ground truth and deterministic scorers**, with a citation check on every answer and a judge only for the few free-text cases. More work up front on case types and scorers, but scores are exact, reproducible and free to recompute.
3. **Human grading.** The gold standard for a small sample, but not repeatable at every change.

## Decision
Option 2, with option 3 used to check it.
- Expected answers are computed by code from the committed cohort bundles, never through the server's tools and never by a model.
- Each case has a type (fact, list, latest, window, classify, absent, out of scope; spec §5) with its own deterministic scorer.
- Every answer gets a citation check: each ref it cites must have been returned by a tool in that run, and the claimed value must match the resource.
- A judge is used only where an answer is meant to be prose, and only after it's been calibrated against hand labels on a sample.
- Refusals, truncations and infra errors are their own outcomes; infra errors go to `errors.jsonl`, not into the score.
- Runs repeat each case and report spread; full trajectories are kept.
- An oracle and a null baseline run through the whole pipeline on every PR.
- A hand-verified sample of cases is part of slice 4.

## Why
Code-derived truth makes the answer key independent of both the server and the models being measured, so a bug in a tool can't also be in the key, and no model is rewarded for sounding like another one. Deterministic scorers make results reproducible and free to re-score from saved trajectories. The citation check catches the failure that matters most here, invented facts, without a judge. Keeping errors apart and reporting spread stops plumbing problems or noise from looking like model differences.

## Consequences
- Case types and scorers take real time to build; that's budgeted in the appetite.
- Questions are limited to what code can answer exactly, plus a few free-text cases. That's a deliberate narrowing: it's what the claims need.
- Changing a scorer or the case set makes earlier scores incomparable; the run report records the case-set and scorer versions.
- Revisit the judge's role if free-text cases grow; any judge change gets recalibrated.
