# 99-implementation-readiness-review.md

# Component PRD Readiness Review — for Agent Implementation

**Goal assessed against:** confidence-scored lineage collection with impact
analysis, implementable by a coding agent from these docs + HLD/LLD/Test Suite.
**Verdicts:** READY (agent can start) · GAPS (agent can start; listed items
must land before that FR is built) · BLOCKED-ON-CTX (needs enterprise/catalog
input first — kept OPEN, never invented).

## Review method

Each PRD checked for: (1) every FR has a concrete schema/algorithm in LLD or
in-PRD; (2) all constants have values or a named owner; (3) no hidden
enterprise knowledge is assumed; (4) test suite covers the FRs; (5) the
component's contribution to confidence + impact analysis is explicit.

---

## Per-component verdicts

### L01 URN & Resolver — GAPS + 4 open CTX
Ready: grammar, pipeline order, resolve() types, quarantine semantics, golden-
corpus harness design.
Gaps: **G-L01-1** normalization rules need per-platform case/quote tables
written as data (agent needs the actual tables, not prose). **G-L01-2**
cross-language 4-runtime build is heavy for MVP — candidate descope (Q5).
Open CTX: CTX-01 catalog export schema/mechanism; CTX-02 platform vocabulary
initial values; CTX-03 temp/staging naming patterns in the estate; CTX-04
alias-decision governance approvers.

### L02 Intake & Queues — READY + 2 open CTX
Ready: envelope schema, dedupe, lanes, DLQs, quarantine — all concrete.
Open CTX: CTX-05 Jenkins deploy event real payload + signing scheme; CTX-06
GitHub org/app installation scope for webhooks.

### L03 Orchestration — GAPS
Ready: four workflows + promotion λ, determinants, run ledger.
Gaps: **G-L03-1** repository classification (v3's 9 classes / 7 evidence
levels) is referenced but no component owns it — needs a home (Q4).
**G-L03-2** constants unpinned: decay N, bulk-lane quotas, PRGate latency
budget — add a versioned constants table (proposed values in §Constants).
**G-L03-3** ASL-level workflow specs not yet written; agent needs stage-level
input/output schemas (derivable from LLD §2 — one authoring pass required).

### L04 SCA Engine — GAPS + 2 open CTX
Ready: pipeline, Candidate interface, evidence schema, determinism contract,
residue reasons.
Gaps: **G-L04-1** rule packs need matcher specs as data (per-framework
sink/source signature tables) — the single largest authoring lift; recommend
one pack (Python) fully specified as the template, others follow its format.
Open CTX: CTX-07 SQL dialects actually present in the estate (drives sqlglot
matrix); CTX-08 config-manifest conventions (application.yaml? helm? .env?)
that config-resolution must parse.

### L05 LLM Gateway — GAPS + 1 open CTX
Ready: tiers, cache key, guardrail chain, response/reject schemas, staged
rollout.
Gaps: **G-L05-1** prompt templates v1 + structured-output JSON Schema per
tier must be authored (schema exists in LLD §2.3; prompts don't). **G-L05-2**
budget cap values unset (governance) — implement as config, values OPEN.
Open CTX: CTX-09 enterprise Bedrock gateway endpoint, auth, allowed model ids.

### L06 Runtime Plane — GAPS (Phase 2) + 3 open CTX
Ready: session lifecycle, validator order, observation schema, fingerprints,
deny-twice.
Gaps: **G-L06-1** OTel attribute-extractor mapping table (span attr →
RawName kind) needs authoring as data. **G-L06-2** containment semantics must
be pinned numerically with L07 (see G-L07-1).
Open CTX: CTX-10 Spark platform (EMR/Databricks/k8s) and OL listener install
path; CTX-11 integration-test harness/CI entry points for session grant;
CTX-12 OTel collector topology ownership.

### L07 Consolidation & Confidence — GAPS (one blocking for the end goal)
Ready: merge algorithm (LLD §5), idempotency, lifecycle, band table.
Gaps: **G-L07-1 (blocking for "confidence score")** — bands are ordinal;
"half-step" containment boost is prose, not math. Decision needed (Q1): pure
ordinal bands at MVP, or a numeric score (e.g. 0–100 mapped from mechanism-
agreement weights, calibrated later by the L09 corpus). The scoring function
must be written as a table an agent can implement either way. **G-L07-2**
decay N unpinned (Q6). **G-L07-3** contradiction predicate needs a precise
definition list (transform mismatch normalization rules — when are two
transform strings "different"?).

### L08 Evidence Store — READY + 1 open CTX
Ready: layout, refs, lock classes, cache indexes, rebuild.
Open CTX: CTX-13 retention durations per class (compliance owns; implement as
config, values OPEN).

### L09 Proposal & Review — GAPS + 2 open CTX
Ready: lifecycle, corrections, auto-publish narrowing, corpus.
Gaps: **G-L09-1** auto-publish phase placement (MVP vs P1) materially changes
baseline review load (Q9). **G-L09-2** corpus record schema needs one
authoring pass (fields listed, JSON schema not written).
Open CTX: CTX-14 TAS ownership field mapping (system → reviewer group);
CTX-15 PII/classification flags available from catalog for the auto-publish
exclusion option.

### L10 Fenced Publication — READY
Ready: full protocol, property tests, rebuild, pointer contract. Constants
(namespace retention count) → constants table.

### L11 APIs, UI, PR Gate — GAPS (impact analysis is under-specified)
Ready: query/review API shapes, verdict classes, dashboards.
Gaps: **G-L11-1 (blocking for the end goal)** — impact analysis needs its own
spec section: supported change types (column drop, type change, rename,
dataset removal, transform change), traversal semantics (transitive vs
bounded, Q3), severity mapping (which bands can BLOCK vs WARN), and response
schema. One authoring pass after Q2/Q3. **G-L11-2** SPA information
architecture beyond queues/diff is unspecified — acceptable for an agent with
the API contracts, but wireframes would de-risk review UX.

### L12 Security & Ops — READY + 2 open CTX
Ready: policies-as-code scope, drill calendar, correlation contract.
Open CTX: CTX-16 org paging/severity conventions; CTX-17 org CloudTrail +
Config baseline to inherit.

---

## Constants table (proposed; versioned config, not code)

| Constant | Proposed | Owner | Status |
|---|---|---|---|
| Decay cycles N | 3 consecutive silent analyses | Platform | DECIDED (L07 §15) |
| Auto-publish audit sample | 5% weekly | Governance | pinned in PRDs |
| Audit disagreement breach | 2% rolling 4 weeks | Governance | pinned |
| Join-rate gate | ≥ 80% | Platform | pinned |
| LLM chunk budget | 8k tokens | Platform | pinned |
| LLM $/repo/day, tokens caps | — | Cost governance | OPEN |
| PRGate latency budget | ≤ 60 s p95 in-check | Platform | pinned |
| Bulk-lane per-system quota | — | Ops | OPEN |
| Namespace retention | last 10 versions per env | Ops | proposed |
| Evidence retention per class | — | Compliance | OPEN (CTX-13) |

## Open enterprise/catalog context register (kept open by design)

CTX-01 catalog export schema/mechanism · CTX-02 platform vocabulary values ·
CTX-03 temp/staging name patterns · CTX-04 alias governance approvers ·
CTX-05 Jenkins event payload/signing · CTX-06 GitHub installation scope ·
CTX-07 estate SQL dialects · CTX-08 config-manifest conventions · CTX-09
Bedrock gateway endpoint/models · CTX-10 Spark platform · CTX-11 test-harness
entry points · CTX-12 OTel collector topology · CTX-13 retention durations ·
CTX-14 TAS ownership mapping · CTX-15 catalog PII flags · CTX-16 paging
conventions · CTX-17 org audit baseline.

Rule for the implementing agent: every CTX item is a **named config seam** —
implement the interface, stub with fixtures, never invent enterprise values.

## Build-order recommendation (dependency-safe)

1. L01 resolver (with CTX-01 stub + golden corpus) → 2. L08 store → 3. L02
intake → 4. L03 orchestration skeleton (Incremental only) → 5. L04 SCA
(Python pack first) → 6. L07 merge (band table per Q1 decision) → 7. L09
review + L10 publish → 8. L11 query/impact (per Q2/Q3 spec) → 9. L05 LLM →
10. L06 runtime (P2) → L12 threaded throughout.

## Summary

READY: L02, L08, L10, L12. GAPS: L01, L03, L04, L05, L09, L11 — all
resolvable by authoring passes once Q1–Q9 are answered. BLOCKED-ON-CTX only
where enterprise values are required; all are config seams, none block
starting. The two decisions that shape the end goal: **confidence score
representation (Q1)** and **impact-analysis semantics (Q2/Q3)**.

## Decisions applied (2026-08-04)

- **Q1 DECIDED:** ordinal bands only at MVP; numeric score deferred to
  calibrated P2+. Scoring + containment math pinned in **L07 §15**.
- **Q2/Q3 DECIDED (delegated):** all six change types; bounded sync (≤5) +
  async full closure with notification. Spec written in **L11 §15**.
- **Q4 DECIDED (delegated):** new **L13 Repository Classification PRD**
  created (9 classes, 7-level precedence from v3 slide 06). G-L03-1 closed.
- **Q5 DECIDED:** resolver MVP = Python + JVM; Node/Go in P1 (L01 updated).
- **Q6 DECIDED:** N = 3 (L07 §15).
- **Q7 DECIDED:** runtime OTel slice pulled into Phase 1 (L06 updated);
  SDK/Spark/Dask remain Phase 2.
- **Q8 DECIDED (delegated):** auto-publish active at MVP with the 5% sampled
  audit and automatic narrowing; governance sign-off on the policy file is a
  launch gate (L09 updated).
- **Q9 DECIDED (delegated):** implementation stack for the agent — **monorepo**,
  **AWS CDK (TypeScript)** for IaC, **Python** for services/Lambdas/Fargate
  tasks, resolver as Python + JVM libraries from one rule source. Pin in each
  component's Implementation Notes as authored.
- **CTX-01…CTX-17 remain OPEN** as named config seams; the agent implements
  interfaces + fixtures and never invents enterprise values.

### Updated verdict summary

READY: L02, L08, L10, L12, **L13 (new)**. READY after this pass: L01, L06,
L07, L09, L11. Remaining authoring lifts before the affected FRs are built:
G-L01-1 (platform case/quote tables), G-L03-3 (stage-level ASL specs),
G-L04-1 (Python rule-pack matcher tables as the template), G-L05-1 (prompt
templates v1), G-L06-1 (OTel attribute mapping table), G-L09-2 (corpus JSON
schema). None block starting the build order in §Build-order.

## Addendum (infrastructure gap closed)

**L14 Infrastructure & Deployment Spec** created: account/network topology,
per-component CDK stack mapping, CI/CD pipeline + canary/blue-green rollout,
consolidated fault-tolerance table, capacity envelope, cost controls. This
closes the "where is infrastructure/deployment defined" gap; interfaces and
schemas remain normative in LLD §§1–3, 6 and per-PRD §§6–7.

## Addendum 2 (NFR/resiliency consolidation)

**L15 NFR & Resiliency Spec** created: consolidates every per-PRD §5 SLO into
one table and adds the three missing pieces — availability tiers with error
budgets (T1 99.9% interactive / T2 99.5% pipeline / T3 99% batch, *proposed*),
RPO/RTO per data class, a dependency degradation-mode matrix, explicit
backpressure rules, and a claim→test verification map. New authoring lift:
one fault-injection fixture per degradation row (Test Suite §7).

## Addendum 3 (delivery structure)

**L16 Delivery Plan** created. DE verdict: PRD numbering is data-flow order,
NOT implementation order; component cut kept as spec-of-record (correct
contract seams for parallel teams). Added: hard dependency graph + critical
path, 7 interface freezes (F1–F7), 4 ownership tracks (Foundations/Flow/
Engines/Trust&Experience), vertical-slice milestones M0–M4 with exit
criteria. Structural corrections: contracts lib promoted to first-class
shared artifact; L03+L13 delivered as one unit; PR-gate slice single-owned.
The §Build-order in this review is superseded by L16.
