# Prototype Coverage

This matrix is an honest M1 slice, not a claim that every production requirement is complete. “Implemented” means exercised locally by code and tests. “Fixture adapter” preserves the component seam with deterministic data. “Interface only” means downstream contracts exist without the enterprise integration. “Deferred” means explicitly outside this prototype.

| Component | Status | Included in the prototype | Out of prototype scope |
|---|---|---|---|
| L01 URN and resolver | Implemented | Canonical URN grammar, ordered catalog resolution, catalog-backed aliases, element validation, quarantine, snapshot and resolver determinants | JVM build, per-platform case/quote data, enterprise catalog export, governed alias mutation |
| L02 intake and queues | Implemented | HMAC verification, immutable event identity, dedupe, normalized envelope, correlation, lane policy, quarantine | GitHub/Jenkins receivers, EventBridge/SQS/DLQ infrastructure, TTL expiry, load targets |
| L03 orchestration | Implemented | Synchronous Incremental workflow, durable ordered run stages, UNKNOWN classification block, approval continuation | Step Functions ASL, Baseline/PRGate/Nightly workflows, failed-stage redrive, quotas and scheduling |
| L04 SCA engine | Implemented | Deterministic Python AST matcher for seeded `read_dataset`/`write_dataset`, exact citations, transforms, residue, byte replay | Full framework rule packs, SQL dialect matrix, config-manifest substitution, hostile-repo performance envelope |
| L05 LLM gateway | Interface only | LLM provenance kind, confidence behavior, evidence-store namespace and rejection contract are accepted by consolidation | Bedrock call, prompt templates, tier budgets, cache, guardrail execution and staged rollout |
| L06 runtime observation | Fixture adapter | Complete element-scope runtime assertions join SCA edges and independently set corroboration | Session JWTs, OTel extraction, Kinesis, Spark/Dask/SDK emitters, prod hard-deny probe |
| L07 consolidation and confidence | Implemented | Stable edge keys, append-only versions, mechanism dedupe, ordinal band table, separate corroboration, transform conflict retention | Estate-scale decay/re-derivation and complete contradiction rule catalogue |
| L08 evidence store and cache | Implemented | Canonical SHA-256 JSON, safe typed keys, write-once local objects, indexed references, checksum verification | S3 Object Lock, replication, lifecycle retention, LLM cache and enterprise KMS policy |
| L09 proposals and review | Implemented | Deterministic proposal diff, server state machine, optimistic review lock, immutable approvals, correction successors, audit | MVP auto-publish sampling/narrowing, TAS routing, calibration corpus and authorization |
| L10 fenced publication | Implemented | Checksummed manifest, monotonic reservation, inactive staging, content verification, atomic pointer swap, stale-writer rejection, audited rollback | Neptune/OpenSearch projections, deployment promotion, namespace expiry and rebuild drill |
| L11 APIs, UI and PR gate | Implemented | Local API, common errors, run/review/operations UI, accessible lineage graph, edge provenance, bounded version-pinned impact | Async full-closure jobs, PR check integration, identity-aware RBAC/ABAC, quarantine triage workflow, production SLOs |
| L12 security, observability and operations | Interface only | Sanitized intake quarantine, correlation propagation, local audit ledger, bounded inputs, CORS limited to local Vite origins | AWS IAM/KMS/WAF, CloudTrail/Config, OTel export, paging, DR and security drills |
| L13 repository classification | Implemented | Versioned nine-class treatment table, evidence precedence, same-level conflict to UNKNOWN, immutable decision record | Estate inventory, path-level monorepo routing UI, overrides/expiry and policy preview |
| L14 infrastructure and deployment | Deferred | Reproducible native local processes and locked dependencies | AWS CDK stacks, accounts/VPC endpoints, CI/CD canary/blue-green rollout, capacity and cost controls |
| L15 NFR and resiliency | Interface only | Deterministic replay, fencing/tamper tests, failure contracts, bounded depth, local build/test gates | Availability/error-budget measurement, load tests, RPO/RTO drills and degradation fault injection |
| L16 delivery plan | Implemented | Recommended M1 vertical slice from one signed push through manual proposal, fenced publish and query, plus review SPA polish for demonstration | M0 enterprise pipeline, M2 breadth/auto-publish, M3 PRGate/OTel, M4 full runtime and DR program |

## Walking-skeleton mapping

```text
Signed push (L02)
  → classification + run ledger (L13/L03)
  → Python evidence + catalog URNs (L04/L01)
  → immutable SCA and runtime fixture evidence (L08/L06)
  → confidence merge (L07)
  → manual proposal and approval (L09/L11)
  → fenced projection v2 (L10)
  → lineage and impact query (L11)
```

The local design intentionally pulls UI and impact forward from L16 M2 so the prototype can demonstrate and verify the complete product decision loop. That does not redefine the production delivery plan.
