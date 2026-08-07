---
type: Note
title: LLM mechanism is modelled but not implemented
description: The LLM evidence mechanism appears throughout the type system, confidence rules and reason codes, but no component anywhere actually produces an LLM assertion.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/prototype-coverage.md
tags: [llm, scope, gap, confidence]
timestamp: 2026-08-07T16:49:03Z
---

# LLM mechanism is modelled but not implemented

Reading the confidence code gives the strong impression of a three-mechanism
system. It isn't, yet.

`LLM` appears in `MECHANISMS`, in `derive_band()`'s branch order, in
consolidation, in PR-gate reason codes (`LLM_ONLY_BLOCK_EVIDENCE`), and the
evidence store reserves `llm` and `llm-reject` object kinds. But **no producer
exists**. There is no LLM analyzer in `services/`. Residue from static analysis
is recorded with reason `LLM_NOT_CONFIGURED`.

Runtime evidence is only slightly better off: it's synthesized from SCA output
by a fixture adapter (`_runtime_fixture()`), not ingested from real telemetry.

## Why this matters when reading the code

Every `HIGH` band you see in this deployment comes from `{SCA, RUNTIME}` where
the runtime half was manufactured from the SCA half. The two mechanisms whose
*independence* justifies the `HIGH` band are, in the prototype, the same
mechanism twice. That's fine for demonstrating the pipeline — it is not evidence
that the confidence model works.

Do not use prototype band distributions to calibrate anything.

## Not an oversight

This is declared scope, not drift. `docs/prototype-coverage.md` rates L05 as
"Interface only — no real Bedrock call, seams preserved" and L06 as "Fixture
adapter". The seams are deliberately left in place so a real producer can be
dropped in without touching the confidence logic.

## Related

* [Confidence is agreement, not a score](/decisions/confidence-is-agreement-not-a-score.md) - the rules that reference a mechanism nothing produces
* [Impact severity gating](/references/impact-severity-gating.md) - the guarantee that an LLM-only edge can never block, currently untestable end to end
* [Lineage Collector Prototype](/projects/lineage-collector-prototype.md) - overall implemented-vs-declared state

## Citations

1. [docs/prototype-coverage.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/prototype-coverage.md)
2. [domain/confidence.py:6](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/domain/confidence.py) — `MECHANISMS`
