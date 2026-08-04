# 16-delivery-plan-and-dependencies.md

# L16 Delivery Plan — Dependencies, Team Structure, Milestones

## 1. DE Review Verdict: keep the PRDs, add this plan

The L-numbering is **data-flow order, not implementation order** — following
it serially would build engines before anything can consume them and leave
integration risk for the end.

Reviewed against "hand to a team of 4": the component cut is **correct as the
spec-of-record** — each PRD owns one contract boundary, which is exactly what
parallel teams need. Re-cutting PRDs into use-case documents would duplicate
requirements across owners and blur the contract seams (the reference PRDs
have the same property). What was missing is the **delivery mapping**: tracks,
dependency graph, milestone slices. Three structural corrections are applied
here rather than by rewriting PRDs:

1. **Contracts library is a first-class shared artifact** (JSON Schemas,
   generated models, EvidenceRef, correlation lib — today implicit in
   L01/L08/L12). It gets an owner and freezes first.
2. **L03↔L13 are one delivery unit** (orchestration is untestable without
   classification decisions; they ship together even though the specs stay
   separate).
3. **The PR-gate slice spans L03+L11** — single owner per slice rule applies
   (Track B builds it end-to-end; Track D reviews the rendering contract).

## 2. Hard Dependency Graph (build-time)

```text
FOUNDATIONS (no upstream)
  contracts lib ──┬─→ everything
  L08 store client┤
  L01 resolver ───┤            L12/L14 platform baseline (day 1, continuous)
                  │
INTAKE/FLOW       ▼
  L02 intake ──→ L03 orchestration ←── L13 classification
                  │
ENGINES           ▼
  L04 SCA ──residue contract──→ L05 LLM
  L06 runtime (P1 OTel slice; P2 full)
                  │
TRUST             ▼
  L07 merge ←── evidence schemas (L04/L05/L06)
  L07 ──→ L09 review ──→ L10 publish
                  │
EXPERIENCE        ▼
  L11 APIs/UI ←── L09, L10 pointers, L03 run ledger
```

Critical path: **contracts → L01/L08 → L02/L03 → L04 → L07 → L09/L10 → L11**.
L05, L06, L13-deepening, and dashboards hang off it without blocking it.

## 3. Interface Freeze Order (what unblocks whom)

| Freeze | Contract | Unblocks |
|---|---|---|
| F1 (week 1) | EvidenceRef, correlation fields, EventEnvelope | everyone |
| F2 | resolve() types + URN grammar | L04/L05/L06/L07 |
| F3 | ScaEvidenceFile + residue | L07, L05 |
| F4 | ConsolidatedEdge + bands + corroboration | L09, L11 |
| F5 | Proposal/approval records | L10, L11 |
| F6 | Pointer read + manifest | L11, promotion λ |
| F7 | LlmResponse/reject; Observation | L07 extensions |

Freezes are semver-pinned in the contracts lib; a post-freeze change is a
major bump with a migration note — the Test Suite §5 pacts enforce this.

## 4. Team of 4 — Ownership Tracks

| Track | Owner | Components | First deliverable |
|---|---|---|---|
| A · Foundations & Identity | Eng 1 | contracts lib, L01, L08, L12+L14 baseline | F1/F2 freezes; store+resolver with golden corpus & fixtures |
| B · Flow | Eng 2 | L02, L03+L13 (one unit), PR-gate slice (with D) | intake→Incremental skeleton run with stub engine |
| C · Engines | Eng 3 | L04 (Python pack first), L05, L06 (P1 slice) | SCA on seeded repos emitting real evidence |
| D · Trust & Experience | Eng 4 | L07, L09, L10, L11 | merge+manual review+publish+one-hop query |

Cross-cutting rules: L12/L14/L15 conformance is owned by A but implemented by
each track in their components; every track lands its Test Suite sections
with the code (no separate QA phase).

## 5. Milestones (vertical slices, not component order)

**M0 · Foundations (A leads, ~weeks 1–3)** — contracts lib + F1/F2; L08
buckets/tables + client; L01 py+jvm with golden corpus; CDK pipeline deploys
to staging. *Exit: another track lands a service through the pipeline using
the client libs.*

**M1 · Walking skeleton (all tracks, ~weeks 3–8)** — one real push through
the whole spine: L02 intake → L03 Incremental → L04 (Python pack, seeded
repo) → L07 merge (bands per L07 §15) → L09 manual proposal → L10 fenced
publish → L11 one-hop query. No LLM, no classification breadth, no UI polish.
*Exit: Test Suite §4 "Incremental push" green end-to-end in staging; join-rate
metric emitting.*

**M2 · Breadth (weeks 8–16)** — L13 full taxonomy + Baseline workflow; L04
remaining packs + parsers; L05 T1/T2 with guardrails; L09 auto-publish +
narrowing; L11 impact API (§15 spec) + review SPA; replay/rebuild drills.
*Exit: MVP launch-gate dashboard live (join ≥80%, cache ≥90%, audit &lt;2%);
baseline on 50 real repos.*

**M3 · Phase 1 (weeks 16–22)** — PR-gate slice (B+D); deploy-promotion λ;
L06 OTel slice (sessions+validator+extractor); OpenSearch projection; Node/Go
resolver builds. *Exit: PR verdicts on a pilot org; corroboration lifting
bands in staging.*

**M4 · Phase 2** — L06 full emitters (SDK/Spark/Dask); Nightly + LLM T3;
registry re-derivation; DR + degradation fixture rotation complete.

## 6. Dependency Risks the Plan Absorbs

- **CTX seams (open):** every CTX item sits behind an interface built in
  M0/M1 with fixtures — enterprise answers slot in without rework.
- **L05 late?** The spine never blocks on it (skip-with-record is designed
  in); LLM lands in M2 against a frozen residue contract from M1.
- **Reviewer economics unknown until M2:** auto-publish is MVP-active, so
  baseline volume in M2 is the first real measurement — narrowing policy is
  the safety valve.
- **Single-owner bottleneck (A):** F1/F2 are deliberately small; A pairs with
  D on L08 to parallelize.

## 7. Definition of Done (plan level)

M0–M2 exit criteria met with retained evidence; every freeze recorded in the
contracts lib changelog; each PRD's Definition of Done checked off by its
track owner; launch review consumes only the L15 gate dashboards.
