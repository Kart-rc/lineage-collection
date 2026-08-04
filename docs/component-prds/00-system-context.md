# 00-system-context-and-architecture.md

# System Context and Architecture

**Status:** Normative shared specification
**Scope:** SCA, LLM, and runtime lineage collection through consolidation, review, and fenced publication
**Companions:** Lineage PRD · Lineage HLD · Lineage LLD · Lineage Test Suite (this project)

## 1. Product Boundary

The platform collects element-level lineage across ~10,000 repositories through
three independent engines — static code analysis, LLM inference, and runtime
observation — merges their assertions by canonical URN into one common schema,
and publishes only human- or policy-approved edges to versioned graph
projections. It owns lineage metadata and audit history. It does not own source
repositories, the enterprise catalog, deployment systems, test execution, or
data-plane payloads.

## 2. Authoritative Architecture Rules

> EventBridge routes, SQS buffers, Step Functions coordinates, Fargate
> analyzes, S3 preserves, DynamoDB controls, Neptune traverses, and a human
> (or an audited auto-publish policy) approves.

- The enterprise catalog is the URN authority; one shared resolver library
  (L01) normalizes every name at intake. A guessed URN is a defect.
- S3 evidence, proposals, and accepted manifests are immutable truth;
  Neptune/OpenSearch are rebuildable approved projections only.
- The LLM answers only what SCA could not; it proposes and never gates.
- Runtime observation is integration-test only, metadata only; production is
  hard-denied at IAM and again at validation.
- Agreement across independent mechanisms sets confidence; absence is never
  negative evidence; provenance is retained verbatim.
- Parser-exact SCA edges may auto-publish under a sampled-audit policy; all
  other material change requires explicit human approval.

## 3. Component Topology

```text
EXT (GitHub, Jenkins, TAS, catalog, schedules)
  → L02 Intake & Queues → L03 Orchestration (informed by L13 Classification)
       L03 → L04 SCA Engine ──┐
       L03 → L05 LLM Gateway ─┼→ L08 Evidence Store → L07 Consolidation
       L06 Runtime Plane ─────┘        (L01 Resolver used by L04/L05/L06/L07)
  L07 → L09 Proposal & Review → L10 Fenced Publication → L11 APIs & UI
  L12 Security/Operations ─ controls and observes all components
```

## 4. Component Responsibilities

| ID | Component | Owns | Key output |
|---|---|---|---|
| L01 | URN & resolver library | Grammar, normalization, catalog snapshot match, quarantine | `Resolved` / `Quarantined` |
| L02 | Event intake & queues | Signature auth, dedupe, envelope, lane routing | `EventEnvelope` + queue decision |
| L03 | Orchestration | 4 workflows + deploy λ, determinant-bounded scheduling | `CollectionRun` stage history |
| L04 | SCA engine | Parsers, AST rule packs, config resolution, residue | `ScaEvidenceFile` |
| L05 | LLM gateway | Tiered triggers, chunking, cache, guardrail chain | validated `LlmResponse` / rejects |
| L06 | Runtime observation | Sessions, four emitters, validator, stream | `Observation` evidence |
| L07 | Consolidation & confidence | URN merge, bands, conflict/decay/rename | `ConsolidatedEdge` |
| L08 | Evidence store & cache | S3 truth layout, Object Lock, cache index | `EvidenceRef`, cache records |
| L09 | Proposal & review | Proposal lifecycle, corrections, auto-publish policy | `Proposal`, approval records |
| L10 | Fenced publication | Lock, versioned namespaces, pointers, rebuild | `AcceptedManifest`, active pointer |
| L11 | APIs & review UI | Query/impact APIs, review SPA, PR-gate rendering | user-visible approved lineage |
| L12 | Security & operations | IAM/KMS, prod hard-deny, correlation, DR | audit, alarms, recovery evidence |
| L13 | Repository classification | 9-class taxonomy, 7-level evidence precedence, path routing | `ClassificationDecision` |
| L14 | Infrastructure & deployment | CDK stacks, accounts/network, CI/CD, fault tolerance, DR | deployable platform |
| L15 | NFR & resiliency spec | consolidated SLOs, availability tiers, RPO/RTO per class, degradation matrix | launch-gate source of truth |
| L16 | Delivery plan | dependency graph, interface freezes, 4-track ownership, milestones | build order |

## 5. Canonical Flows

Baseline, incremental, PR-gate, runtime-corroboration, and publication flows
are normative in **Lineage HLD §§1, 4–8**; hop-level payloads and the merge
algorithm are normative in **Lineage LLD §§2, 5**. Component PRDs reference,
never restate, those flows.

## 6. Platform Invariants

1. Every trigger is mapped to a run, deduplicated, or quarantined — never
   silently dropped.
2. Every emitted name resolves to a catalog-backed URN or is quarantined with a
   gap report.
3. Every consolidated edge carries verbatim per-mechanism provenance and a
   derivable confidence band.
4. LLM-only provenance never gates a PR and never reaches the highest band.
5. Incomplete or absent runtime evidence never demotes an edge; decay requires
   N observed-silent cycles.
6. Accepted manifests and approval records are never updated in place.
7. The graph pointer advances only under a valid fencing token.
8. Projections can be deleted and rebuilt from S3 without losing approved state.
9. Production cannot grant runtime sessions or accept observations.

## 7. Launch Gates (measured, not asserted)

URN join rate ≥ 80% · LLM cache hit ≥ 90% · auto-publish audit disagreement
&lt; 2% · review queue depth below reviewer capacity · 10,000-event burst
absorbed without loss · projection rebuild drill passes.

## 8. Correlation Contract

Every event, log, evidence file, proposal, and publication carries:
`correlationId`, `runId`, `repo`, `digest`, `env`, `system`, and where
applicable `sessionId`, `proposalId`, `namespaceVersion`, `resolverVersion`,
`snapshotId`, `rulesetVersion`, `promptVersion`. Unknown fields are absent,
never empty strings.
