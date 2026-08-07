# B10 — Consolidation and confidence worker

Normative requirements: [L07 consolidation](../component-prds/07-consolidation-and-confidence.md).

## Ownership

Track D owns identity merge, provenance union, confidence/corroboration matrix, conflict handling,
rename/partition policy and deterministic proposal-ready edge output.

## Boundary

Consume normalized SCA, LLM and complete exact-artifact runtime assertions, merge by lineage identity
and write versioned candidate edges. It cannot approve or publish.

## Contracts

Inputs are immutable evidence references plus exact determinants. Outputs are strict consolidated
edges with stable edge/provenance IDs, band, corroboration, conflict, transform and publishability.

## State and failure model

Merge is idempotent and commutative for an assertion set. Duplicates add no provenance; malformed or
conflicting evidence is quarantined/marked. Incomplete runtime never promotes confidence; dataset
corroboration cannot masquerade as element corroboration.

## Data ownership

B10 owns consolidated edge versions, provenance membership and merge decisions. Source evidence is
owned by B06-B09; proposal lifecycle and active graph are B11/B12.

## Infrastructure bill of materials

Consolidation Lambda, DynamoDB edge/idempotency ledger, S3 evidence reads/results, input lane/DLQ,
KMS, reserved concurrency, logs, traces, alarms and optional scheduled decay/reconciliation target.

## Local adapter

An in-process deterministic consolidator and SQLite ledger run property, permutation, duplicate,
runtime and differential fixtures under the same contracts.

## Security and privacy

Read only approved evidence classes, validate references/checksums, condition writes by identity and
avoid logging evidence bodies. Policy changes are versioned and audited.

## SLOs

Zero identity divergence under replay/permutation is mandatory. Throughput, decay and partition
targets require representative corpora; local fixture timing is not a production claim.

## Observability

Emit inputs/outputs, duplicate/conflict/quarantine, band changes, corroboration mechanism, runtime
join, merge latency, ledger conflicts and policy version per correlation.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B10-AC-001 | L07 deterministic confidence | Duplicate and permuted assertions converge to one byte-identical edge version with exact band/conflict/corroboration; incomplete evidence never promotes | Every PR property gate |

## Deployment and rollback

Replay the corpus in shadow for a new policy, compare edge diffs, then canary the worker alias.
Rollback restores prior policy/alias; re-derivation writes new versions rather than mutating history.

## Dependencies

B01-B03, B05 and evidence from B06-B09. Confidence thresholds, catalog classification flags and
decay/narrowing ownership are versioned policy seams.

## Definition of Ready

Identity key, complete band matrix, conflict rules, runtime semantics, property corpus and migration
behavior are reviewed.

## Definition of Done

Permutation/duplicate/differential tests pass; every conflict is visible; policy is versioned;
resources/alarms/rollback and `B10-AC-001` evidence exist.
