---
type: Reference
title: URN Grammar and Resolver Library
description: L01 owns the canonical URN grammar and deterministic name resolution against a pinned catalog snapshot, quarantining anything it cannot prove.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/01-urn-and-resolver-library.md
tags: [lineage, component-prd, urn, resolver, identity]
timestamp: 2026-08-14T11:30:00Z
---

# URN Grammar and Resolver Library

## Purpose

L01 gives every engine the same meaning for a dataset and element. It owns the grammar `urn:ldp:{env}:{platform}:{system}:{dataset}#{element}`, a nine-step versioned normalization pipeline, and deterministic resolution against a pinned, content-addressed enterprise-catalog snapshot. Resolution happens at intake, never at emit; ambiguity is surfaced, never hidden. P0 foundation — every edge, evidence file, and projection depends on stable identity.

## Key requirements and invariants

- 100% of persisted assertions carry catalog-resolved URNs or an explicit quarantine record with reason and candidates; returning a URN not in the pinned snapshot (guessing) is forbidden.
- Deterministic: identical (raw name, context, resolverVersion, snapshotId) inputs produce byte-identical results across all embedding languages (MVP: Python + JVM from one rule source of truth).
- Environment is inside identity — prod and staging URNs never compare equal; cross-env `sameAs` is derived, never stored as identity.
- Pipeline handles JDBC decode, shard collapse to template URNs, temp/staging elision (temp hop kept in provenance only), and view mapping to catalog URNs plus DERIVES edges.
- Zero matches → UNKNOWN quarantine with gap report; 2+ equal-precedence matches → AMBIGUOUS quarantine with all candidates — never a similarity pick.
- Config substitution is all-or-quarantine; no partial resolution.
- A golden corpus is CI-gating; any output diff requires approval and a determinant bump.
- Launch gate: URN join rate (runtime datasets matching SCA URNs) ≥ 80%.

## Interfaces

`resolve(raw, ctx) → Resolved { urn, catalogRef, rulesApplied } | Quarantined { raw, reason, candidates, gapReport }`; batch resolution preserves order under one snapshot. Snapshot lifecycle: hourly pull → contract check → content-address → atomic pin; a run observes exactly one snapshot. Quarantine triage adds alias decisions to the next snapshot build — it never mutates a pinned snapshot.

## Failure semantics and constraints

Catalog outage never blocks intake — last-good snapshot serves with a staleness alarm at 3h; a snapshot failing the catalog contract is refused, alarmed, and last-good retained. Cached single resolution ≤ 5 ms p95; 1,000-item batch ≤ 300 ms p95. Credentials in connection strings are stripped at decode and never persisted.

## Related

* [System Context and Architecture](/references/system-context.md) - establishes L01 as the URN authority interface for the whole platform
* [Static Code Analysis Engine](/references/sca-engine.md) - L04 resolves every emitted name through L01 at intake under one snapshot pin
* [Consolidation, URN Merge, and Confidence](/references/consolidation-and-confidence.md) - L07 merge keys are resolved URNs only; quarantined assertions never reach the ledger
* [Catalog and Resolver (b02)](/references/b02-catalog-and-resolver.md) - the build unit that delivers this library and snapshot pipeline
* [Lineage Platform Acceptance](/references/lineage-platform-acceptance.md) - golden-corpus CI gate and cross-language equality suite are explicit acceptance gates here

## Citations

1. [01-urn-and-resolver-library.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/01-urn-and-resolver-library.md)
