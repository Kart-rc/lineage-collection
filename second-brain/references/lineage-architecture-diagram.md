---
type: Reference
title: "Lineage Architecture Diagram"
description: "Four-panel visual synthesis of the L01-L16 platform: component map with data flow, AWS production mapping, component-internal state machines and guardrails, and the M1 walking-skeleton push-to-query flow."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Lineage-collection-Architecture/Lineage Architecture Diagram.dc.html
tags: [architecture, diagram, aws, components, confidence-bands, walking-skeleton]
timestamp: 2026-08-14T12:14:00Z
---

# Lineage Architecture Diagram

The single-document visual synthesis of the lineage collection platform, drawn at four altitudes.
Its throughline: three engines (SCA, LLM, runtime), one evidence-first trust chain — "EventBridge
routes, SQS buffers, Step Functions coordinates, Fargate analyzes, S3 preserves, DynamoDB controls,
Neptune traverses, a human — or an audited auto-publish policy — approves."

**Panel 1 — Component map (L01-L16).** External sources (GitHub push, Jenkins CI, TAS runs,
enterprise catalog, schedules) flow through L02 intake and L03 orchestration, informed by L13
classification, into the three engines (L04 SCA on Fargate, L05 LLM gateway on Bedrock —
"proposes, never gates", L06 runtime plane — integration-test only, metadata only). Everything
lands in the L08 immutable evidence store, merges in L07, passes the L09 human gate, publishes
through L10 fencing, and serves via L11 APIs. L01 (URN resolver, "guessed URN = defect") and L12
(security and operations, production hard-deny) cut across all components.

**Panel 2 — Production mapping (per the L14 spec).** Each component named to its AWS service:
API Gateway webhook ingress with signature verify, EventBridge with archive+replay, three multi-AZ
SQS priority lanes (interactive/events/bulk) with DLQs, Step Functions with a promotion Lambda,
Fargate SCA, Bedrock LLM, Kinesis→Firehose runtime, S3 Object Lock with CRR (RPO 15 min / RTO 4 h),
DynamoDB edge ledger and publish lock, Neptune versioned namespaces, OpenSearch P1 projection with
lag watermark. Eight numbered callouts state the invariants: dedupe by eventId (TTL 14d), 10k-event
burst with zero loss, idempotent stage redrive, rebuild from S3 alone (quarterly drill), monotonic
fencing token, bounded traversal ≤ 2 s p95.

**Panel 3 — Component internals.** State machines and guardrails one level deeper:

- L02 event states RECEIVED→AUTHENTICATED→DEDUPED→NORMALIZED→ROUTED, any failure → QUARANTINED(reason).
- L03's four workflows (Incremental/Baseline MVP, PRGate P1 — parsers + cache, no LLM, Nightly P2)
  with pinned determinants (config, ruleset, resolver, snapshot, prompt) and no manual "run now".
- L04 SCA pipeline CHECKOUT→…→EMIT; sqlglot/dbt/OpenAPI parsers, tree-sitter rule packs (Python,
  Java/Kotlin, JS/TS, Go); residue reasons (dynamic-name, reflection, unparseable-type) hand off to LLM;
  same (digest, ruleset, config) → byte-identical evidence.
- L05 tiers (T1 residue, T2 no-grammar, T3 verification, bulk lane only; PR-path rejected by design);
  guardrail order schema → citation exists → URN resolves → budget; cache hit ≥ 90% gate.
- L06 session lifecycle GRANT→READY→OBSERVING→DRAIN→CLOSED; 4 emitters (OTel, SDK, Spark
  OpenLineage, Dask); prod deny ×2 (IAM + validator); names, never values.
- L08 S3 key layout (evidence/sca/{repo}/{digest}/{ruleset}.json etc.) with Object Lock on manifests.
- L07 edgeKey = hash(from[], to, type); the five ordinal confidence bands (LOWEST LLM-alone →
  HIGHEST 3-of-3); edge lifecycle PROPOSED→PUBLISHED→STALE→TOMBSTONED (+CONFLICTING); decay after
  3 silent cycles; rename = tombstone + SAME_AS; conflicts kept verbatim, never averaged.
- L10 fencing protocol MANIFEST→RESERVE+FENCE→STAGE→VERIFY→SWAP|DISCARDED — one DynamoDB
  transaction advances the pointer; rollback is a pointer event, never a rewrite.

**Panel 4 — M1 walking skeleton.** Ten steps from signed push to version-pinned query, each with its
failure semantics (replay → DUPLICATE, classification conflict → UNKNOWN blocks analysis, 0-or->1 URN
matches → quarantine, stale writer can never advance the pointer), ending in PR-gate verdicts
BLOCK/WARN/INFO with traversal depth ≤ 5. A closing table maps local prototype substitutions to
production semantics (in-process SQLite lanes ↔ EventBridge+SQS, write-once directory ↔ S3 Object
Lock, SQLite transactions/versioned projections ↔ DynamoDB/Neptune) — semantics preserved.

## Related

* [System Context and Architecture](/references/system-context.md) - the normative L01-L16 specification this diagram renders visually.
* [Lineage Platform Target AWS Architecture](/references/lineage-platform-target.md) - the canonical production AWS target that panel 2 maps component-by-component.
* [L14 Infrastructure, Deployment, and Fault-Tolerance Specification](/references/infrastructure-and-deployment-spec.md) - the spec panel 2's production mapping is drawn from.
* [Consolidation, URN Merge, and Confidence](/references/consolidation-and-confidence.md) - the L07 rules behind the confidence-band ladder and edge lifecycle shown in panel 3.
* [L10 Fenced Publication and Graph Projections PRD](/references/fenced-publication-and-projections.md) - the full fencing protocol panel 3 compresses into one state machine.

## Citations

1. [Lineage Architecture Diagram.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Lineage-collection-Architecture/Lineage Architecture Diagram.dc.html)
