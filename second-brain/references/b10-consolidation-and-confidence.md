---
type: Reference
title: B10 Consolidation and Confidence Worker
description: Delivers the deterministic worker that merges SCA, LLM, and runtime evidence by lineage identity into versioned, confidence-banded candidate edges ready for proposal.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B10-consolidation-and-confidence.md
tags: [lineage, build-prd, consolidation, confidence, determinism]
timestamp: 2026-08-14T11:30:00Z
---

# B10 Consolidation and Confidence Worker

Track D's evidence-merging engine (normative source: L07). It consumes normalized SCA, LLM, and
complete exact-artifact runtime assertions, merges them by lineage identity, and writes versioned
candidate edges. It is the first "trust tier" unit in the build order and explicitly **cannot
approve or publish** — that belongs to B11/B12.

## What it delivers

Identity merge, provenance union, the confidence/corroboration band matrix, conflict handling,
rename/partition policy, and deterministic proposal-ready edge output. Outputs are strict
consolidated edges with stable edge/provenance IDs carrying band, corroboration, conflict,
transform, and publishability.

## Key invariants

- **Merge is idempotent and commutative** for any assertion set: duplicates add no provenance, and
  permuted inputs converge to one byte-identical edge version (B10-AC-001, a per-PR property gate).
- Zero identity divergence under replay/permutation is a mandatory SLO.
- Incomplete runtime evidence never promotes confidence; dataset-level corroboration cannot
  masquerade as element-level corroboration.
- Malformed or conflicting evidence is quarantined/marked, never silently dropped.
- Re-derivation writes new edge versions rather than mutating history.

## Data ownership and boundary

B10 owns consolidated edge versions, provenance membership, and merge decisions. Source evidence
stays owned by B06-B09; proposal lifecycle and the active graph belong to B11/B12. Inputs are
immutable evidence references (validated by checksum), never copies.

## Infrastructure and constraints

Consolidation Lambda, DynamoDB edge/idempotency ledger, S3 evidence reads, input lane/DLQ, KMS,
alarms; the local adapter is an in-process deterministic consolidator over SQLite running the same
property/permutation/duplicate fixtures. Throughput, decay, and partition SLOs require
representative corpora — local fixture timing is not a production claim. Confidence thresholds,
catalog classification flags, and decay/narrowing ownership are versioned policy seams. Rollout of
a new policy replays the corpus in shadow, compares edge diffs, then canaries the worker alias.

## Related

* [Consolidation and Confidence](/references/consolidation-and-confidence.md) - the normative L07 component PRD; this is the build-unit view of the same component.
* [B11 Proposal, Review and Policy](/references/b11-proposal-review-and-policy.md) - downstream consumer that turns B10's consolidated edge diffs into versioned proposals.
* [B06 SCA Worker and Rule Packs](/references/b06-sca-worker-and-rule-packs.md) - upstream evidence engine whose normalized static assertions B10 merges.
* [B08 Runtime Session and Ingestion](/references/b08-runtime-session-and-ingestion.md) - supplies the complete exact-artifact runtime assertions whose incompleteness B10 must never promote.
* [B05 Classifier and Orchestrator](/references/b05-classifier-and-orchestrator.md) - upstream dependency that schedules and correlates the evidence runs B10 consumes.

## Citations

1. [B10-consolidation-and-confidence.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B10-consolidation-and-confidence.md)
