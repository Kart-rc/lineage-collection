---
type: Reference
title: Consolidation, URN Merge, and Confidence
description: L07 unions assertions from the three engines by URN triple, computes agreement-based confidence bands, and manages the full edge lifecycle without ever destroying information.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/07-consolidation-and-confidence.md
tags: [lineage, component-prd, consolidation, confidence, merge]
timestamp: 2026-08-14T11:30:00Z
---

# Consolidation, URN Merge, and Confidence

## Purpose

L07 is the architectural seam. It merges SCA, LLM, and runtime assertions into an edge ledger keyed hash(from[], to, type), computes agreement-based confidence on two axes (structural, derivational), and manages the edge lifecycle: late arrival, disagreement, decay, rename, re-merge, and the proposal handoff. It never destroys information and never writes the graph projections directly.

## Key requirements and invariants

- Merge is idempotent by provenanceId and commutative: replaying any assertion N times in any engine order yields one provenance entry and a stable version. Provenance is append-only — never deleted or rewritten.
- Bands (pinned decision: ordinal only at MVP, no numeric score): LOWEST < SINGLE < MEDIUM < HIGH < HIGHEST. 3-of-3 agreement → HIGHEST; SCA+runtime → HIGH; +LLM → MEDIUM; single mechanism → SINGLE; LLM-only caps at LOWEST. Every band is re-derivable from provenance alone.
- Containment made precise: bands are computed only from element-level assertions. Dataset-level runtime containment never changes the band; it sets a separate `corroboration: NONE | DATASET | ELEMENT` field. PR WARN rendering may treat (SINGLE, DATASET) as MEDIUM-equivalent, but BLOCK decisions read the band alone.
- Contradictions are never averaged: two transform assertions conflict iff both are exact|probable and their normalized forms differ (sqlglot/AST canonical trees where parseable). Conflicts, including SCA-WRITE vs runtime-observed absence across ≥ N COMPLETE sessions, go to the review queue as CONFLICTING within one merge cycle, provenance intact.
- Decay, never demotion-on-absence: N = 3 consecutive silent analyses of the asserting mechanism marks its provenance stale, recomputes the band, sets STALE — never deletes.
- Rename: tombstone the old URN, mint new, link SAME_AS, carry human corrections forward by catalogRef.
- Late arrival on a PUBLISHED edge emits proposal v+1 through the normal gate — never a direct projection write. Parser-exact class (exact transform + SCA provenance + no conflict) is computed here and stamped for L09 auto-publish.

## Interfaces and constraints

Consumes versioned evidence files (unknown schema versions rejected, producer contract alarm) and catalog schemas; produces `ConsolidatedEdge` records, proposal attachments, and join-rate/corroboration metrics. Edge status: `PROPOSED → (AUTO_PUBLISHED | PUBLISHED) → STALE → TOMBSTONED`, CONFLICTING reachable from any active state; publish transitions only via L10 manifest feedback. Merge ≤ 60 s p95 for median incremental scope; the ledger is rebuildable from S3 evidence and the rebuild drill must show zero divergence.

## Related

* [Static Code Analysis Engine](/references/sca-engine.md) - SCA evidence is the deterministic input and source of the parser-exact auto-publish class
* [LLM Inference Gateway](/references/llm-inference-gateway.md) - LLM edges arrive guardrail-validated and are band-capped at LOWEST when uncorroborated
* [Runtime Observation Plane](/references/runtime-observation-plane.md) - runtime observations drive corroboration, containment, and the runtime conflict predicate
* [Consolidation and Confidence (b10)](/references/b10-consolidation-and-confidence.md) - the build unit implementing the merge algorithm, band tables, and lifecycle machine
* [Proposal Review and Autopublish](/references/proposal-review-and-autopublish.md) - L09 receives proposal attachments, auto-publish stamps, and round-trips corrections as edge v+1

## Citations

1. [07-consolidation-and-confidence.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/07-consolidation-and-confidence.md)
