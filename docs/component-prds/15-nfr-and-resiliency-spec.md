# 15-nfr-and-resiliency-spec.md

# L15 Non-Functional Requirements and Resiliency Specification

## 1. Document Control

| Field | Value |
|---|---|
| Component | L15 (cross-cutting; consolidates + extends per-PRD §5 NFR tables) |
| Status | Draft — values marked *proposed* need owner sign-off |
| Criticality | P0 — launch gates derive from this document |
| Primary owner | Lineage platform team + operations |
| Required approvers | Architecture, operations, cost governance |
| Authoritative sources | per-PRD §5 NFRs, L12, L14 §§7–8, 00-system-context §7; new material: §§3–6 below |

## 2. Consolidated Performance SLOs (existing, single table)

| Path | SLO | Source |
|---|---|---|
| Intake ack | ≤ 500 ms p95 @ 100/s sustained | L02 |
| Burst absorption | 10,000 events, zero loss | L02 |
| PRGate verdict | ≤ 60 s p95 in-check, under bulk load | L03/L14 |
| Incremental push→proposal | ≤ 30 min p95 (excl. review) | L03 |
| Baseline | 10k repos within planned window | L03 |
| SCA median repo | ≤ 10 min | L04 |
| LLM T1 round-trip | ≤ 30 s p95 incl. guardrails | L05 |
| Merge stage (median incremental) | ≤ 60 s p95 | L07 |
| Resolver single/batch | ≤ 5 ms / ≤ 300 ms p95 | L01 |
| Publish stage→swap | ≤ 5 min median | L10 |
| Projection visibility | 95% of approvals ≤ 60 s | L10 |
| One-hop query / proposal view | ≤ 2 s p95 | L11 |
| Evidence ref read | ≤ 100 ms p95 | L08 |

## 3. Availability Tiers and Error Budgets (*new — proposed*)

| Tier | Services | Monthly availability | Error budget behavior |
|---|---|---|---|
| T1 interactive | PRGate path, query API, review API | 99.9% | budget burn > 25%/week → feature freeze on that service, reliability work only |
| T2 pipeline | intake, orchestration, engines, merge, publish | 99.5% measured as *runs completing without operator action*; queues mask short outages | burn alarm at 50%; DLQ growth counts against budget |
| T3 batch/deferred | nightly, LLM T3, backfill, canaries | 99% weekly completion | missed window → next window, alarmed |

Rationale: the pipeline is asynchronous by design — availability is defined
as work *not lost and not stuck*, never as synchronous uptime.

## 4. Durability, RPO/RTO per Data Class (*new — consolidates L08/L12*)

| Data class | Durability mechanism | RPO | RTO |
|---|---|---|---|
| Truth (evidence, proposals, approvals, manifests) | S3 + Object Lock + cross-Region replication | 15 min | 4 h |
| Control state (ledgers, pointers, dedupe, cache indexes) | DynamoDB PITR + on-demand backup | 15 min | 4 h |
| Projections (Neptune, OpenSearch) | none needed — rebuilt from manifests | n/a (derived) | ≤ 8 h full rebuild (*proposed*, drill-verified) |
| Catalog snapshots | re-pullable + last-good retained | 1 h (snapshot cadence) | immediate (serve last-good) |
| Telemetry/audit | CloudTrail/CW retention policy | per org baseline (CTX-17) | n/a |

## 5. Degradation-Mode Matrix (*new*)

What the platform does — and what users see — when each dependency fails:

| Failure | Automatic behavior | User-visible state | Invariant held |
|---|---|---|---|
| Catalog export down | serve last-good snapshot; age alarm | banner: snapshot age on triage views | no guessed URNs |
| GitHub webhooks down | gap detected vs archive; replay on recovery | timeline shows gap window | no silent loss |
| Bedrock gateway down | LLM stage skip-with-record; re-merge later | edges arrive later, band updates | Incremental still completes |
| Kinesis throttle | emitter buffering + drain manifest reconcile | session may close INCOMPLETE | incomplete never promotes confidence |
| Neptune down | queries fail T1 budget; publishes stage-and-hold | staleness banner; PR gate → WARN never silent PASS | truth unaffected; rebuildable |
| OpenSearch down (P1) | discovery degraded; traversal unaffected | search unavailable notice | projection disposable |
| DynamoDB throttle | backoff + retry; queues absorb | slower runs, no loss | idempotency preserved |
| Region loss | warm standby: replicate → redeploy → rebuild | RTO 4 h; read-only gap | RPO 15 min on truth |
| Reviewer unavailability | queues age; auto-publish unaffected | queue-age alarm to owning teams | human gate never bypassed |

## 6. Backpressure and Overload (*new — makes L14 behavior explicit*)

- Queues are the only elastic buffer: workflows never accept unbounded
  fan-out (Distributed Map concurrency caps per lane).
- Interactive lane has reserved concurrency; bulk lane is the only lane that
  sheds (defers) under fleet caps.
- LLM gateway sheds by tier (T3 first, then T2) before touching T1 residue.
- Kinesis: no silent sampling — producer backpressure surfaces in session
  manifests.
- Load-shedding events are first-class telemetry, counted against T2 budget.

## 7. Resiliency Verification Map (every claim has a test)

| Claim | Verification | Cadence |
|---|---|---|
| Burst absorption | Test Suite §4 load scenario | nightly |
| Replay w/o duplicates | §4 replay & rebuild | nightly |
| Fencing under contention | §7 publish-lock chaos | weekly |
| Projection rebuild | §7 chaos drill | quarterly |
| Region recovery | L12 DR drill | quarterly |
| Prod hard-deny | §6 live probe | weekly |
| Degradation rows above | fault-injection fixtures per row (*new — add to §7 chaos calendar*) | monthly rotation |
| Error budgets | dashboards from real telemetry | continuous |

## 8. Open Items

- Availability tier percentages and rebuild RTO are *proposed* — operations
  sign-off required (add to constants table).
- Degradation fault-injection fixtures are a new authoring lift for the Test
  Suite (§7) — one fixture per matrix row.
- Org paging/severity mapping stays open as CTX-16.
