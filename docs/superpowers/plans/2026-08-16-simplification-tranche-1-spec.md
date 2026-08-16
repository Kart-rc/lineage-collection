# Simplification Review — Spec for Tranche 1

Date: 2026-08-16. Source: five parallel exploration agents covering UI, backend, collection flows,
confidence/approval, and impact/PR gate. This spec is the authority for the tranche-1 plan
(`2026-08-16-simplification-tranche-1.md`).

## System understanding (condensed)

- Evidence-first lineage collection: signed repo events → deterministic Baseline (B1–B10) /
  Incremental (I1–I10) collection → consolidation into banded edges → proposal `IN_REVIEW` →
  human approval in the UI → fenced publication → exact-artifact deployment promotion (D1–D6) →
  version-pinned lineage/impact queries and a bounded read-only PR gate (P1–P8).
- The dominant complexity pattern is N-plication: three pipeline implementations (local
  `services/orchestration.py`, production `application/stage_handlers.py`, floci Java overrides in
  `tests/integration/floci/`), two HTTP surfaces (`main.py` vs `application/product_api.py`), two
  PR gates, two review/publication stacks (SQLite vs DynamoDB), five graph-traversal copies, and
  duplicated display/validation logic in the frontend.

## Confirmed defects fixed in tranche 1

1. **Local PR-gate verdict precedence is inverted.**
   `apps/api/src/lineage_api/application/workflows/pr_gate.py:153` computes
   `verdict = "WARN" if reasons else ("BLOCK" if calibrated_block else "PASS")`, so any reason
   (e.g. `IMPACT_WARNING`, added whenever `summary.warn > 0`) downgrades a genuine calibrated
   BLOCK to WARN. The production staged gate (`application/pr_gate_execution.py:415`) correctly
   computes `"BLOCK" if calibrated else ("WARN" if reasons else "PASS")`. BLOCK must win.
2. **Frontend confidence projection drifted from the backend.**
   Backend truth (`apps/api/src/lineage_api/domain/product_confidence.py`):
   HIGHEST→VERIFIED 96, HIGH→VERIFIED 92, MEDIUM→PROBABLE 78, SINGLE→PROBABLE 70,
   LOWEST→INFERRED 55. Frontend copy (`apps/web/src/components/review/reviewMeta.ts:4-18`) lacks
   the LOWEST case, so LLM-only edges display as "PROBABLE 70". Consumers also regex the number
   back out of the label (`EdgeInspector.tsx:310`, `LineageCanvas.tsx:24`) and
   `EdgeInspector.tsx:204-209` hardcodes the label strings.
3. **Four competing definitions of "runtime-verified"** in the frontend:
   `reviewMeta.ts:33` (band∈{HIGH,HIGHEST} OR RUNTIME provenance — used by the approval gate),
   `EdgeInspector.tsx:7` and `lineageLayout.ts:19` (band-only), `RunComparePage.tsx:53`
   (corroboration OR provenance). The same edge can be "verified" in the review list and not in
   the canvas legend. One predicate must be shared: the approval-gate semantics
   (band∈{HIGH,HIGHEST} OR any RUNTIME provenance).
4. **`OrchestrationService.approve` hardcodes `env="staging"`**
   (`services/orchestration.py:1726`) while reading `run["env"]` two lines later for the
   graph-checksum lookup — for any non-staging run the read-back would fail. Publish must use
   `run["env"]`.

## Dead code removed in tranche 1 (verify-then-delete)

Backend (`apps/api`):
- `application/coverage.py` (whole 44-line module: `CoverageVerifier`, `CoverageVerification`,
  `CoverageIncompleteError`) + `tests/application/test_coverage.py`. Production coverage checks
  live in `orchestration._coverage_state` and `stage_handlers._coverage`.
- `BaselineWorkflow.plan_repository` + `BaselineFanoutExceeded`
  (`application/workflows/baseline.py`) + their tests. Superseded by
  `AnalyzerRegistry.baseline_plan`.
- `IncrementalWorkflow.load` (`application/workflows/incremental.py:67-69`) — zero call sites.
- Unused dataclasses in `application/models.py`: `CoverageManifest`, `CoverageState`,
  `LineagePackage` (consumed only by the dead `coverage.py`).
- Ports in `application/ports.py` with no implementer and no consumer: `CoveragePort`,
  `ProposalPort`, `PublicationPort`, `CatalogSnapshotPort`, `ReceiptPort`, `TelemetryPort`.
- `application/stage_handlers.py:2289`: `use_cases[("PR_GATE", "P4")]` assignment that is
  unconditionally overwritten a few lines later.
- `DRAFT` proposal state (`domain/proposals.py:11`): never assigned anywhere —
  `ReviewService.create` inserts `IN_REVIEW` directly. Grep confirms `DRAFT` appears in src only
  at that line.

Frontend (`apps/web`):
- ~561 unreachable lines in `src/styles/global.css` (regions ~486-591, 615-756, 846-1078,
  1189-1267, 1348-1587 — pre-redesign layouts) plus `button--approve`/`button--danger`; smaller
  dead sets in `lineage.css`, `runs.css`. Every class must be grep-verified (including dynamic
  `--${...}` template construction) before deletion.
- `StageRail.tsx`: the `StageRail` component, `StageRailProps`, `isTerminalStatus`, private
  `stageNumber` are never imported — only `runStatusTone` is used.
- `StatusPill` `variant` prop (never passed) + its `--outline`/`--solid` CSS.
- `LineageCanvas.tsx:8` `nodeTitle` re-export; `RunComparePage.tsx:11` unused `formatWhen` import.
- `ReviewQueuePage.tsx:124-126` renders "More proposals are available through bounded pagination"
  with no way to load them — remove the misleading note.

## Duplication consolidated in tranche 1

Frontend only (backend consolidation is deferred, see backlog):
- 5 digest shorteners, 3 duration formatters, 2 `statePillClass`, 2 `stageTone`, 2-3
  `stageNumber`/`stageOrder` → one `src/lib/format.ts`.
- Run-state vocabulary + phase-map engine currently exported from the page module
  `RunsPage.tsx` (imported by `RunComparePage.tsx`) → `src/lib/runs.ts`.
- Copy-pasted proposal-diff edge hydration (`ProposalDetailPage.tssx:96-157` and
  `RunComparePage.tsx:160-187`) → one `useProposalEdges(proposal)` hook.

## Global constraints

- Behavior changes are limited to the four confirmed defects above; every other change must be
  behavior-preserving (deletions of unreachable code, moves, renames of module-private helpers).
- Verify-then-delete: no deletion without a fresh grep over `apps/` proving zero references
  outside the deleted code and its own tests. `.worktrees/` is excluded from all greps and is
  never modified.
- Backend suite: `uv run --project apps/api pytest apps/api/tests` must pass after each backend
  task. Frontend suite: `npm test --workspace apps/web -- --run` (vitest) must pass after each
  frontend task; `npm run build --workspace apps/web` (tsc) must pass at tranche end.
- TDD for the behavioral fixes: failing test first, then the fix.
- No new dependencies. No changes under `infra/`, `scripts/`, `packages/contracts/`,
  `second-brain/`, `source-materials/`, or the two design-prototype directories at repo root.

## Deferred backlog (reviewed, deliberately NOT in this tranche)

Ranked; each needs a product/architecture decision or is too invasive for an autonomous pass:
1. Collapse the local pipeline (`services/orchestration.py`, ~2,100 LOC) onto
   `StageDispatcher` + `stage_handlers` with SQLite ports (floci's `SqliteStageProjection`
   proves feasibility). Deletes a whole drift class.
2. Unify the two HTTP surfaces (`main.py` vs `product_api.py`); today the web app calls
   `/api/interactions` and `direction=both`, which only the product surface serves.
3. Retire the synchronous `PRGateWorkflow` + `POST /api/pr-gate/evaluate` (only caller is one
   test) in favor of the staged P1–P8 gate — or wire a real UI/CI consumer; also the local
   `head_reader` lambda is a tautology so `PR_HEAD_SUPERSEDED` can never fire locally.
4. Remove `auto_publishable`/`autoPublishOverride` end to end, or wire a real auto-publish
   policy (`MANUAL_REVIEW_FOR_M1_DEMO` is stamped unconditionally).
5. Drop `FINALIZED` (only the local path writes it; AWS leaves proposals at `APPROVED`; the UI
   tab is permanently empty in production) — cross-stack change.
6. Emit `displayBand`/`percent` from the API (`product_confidence.py` exists for this, but is
   only called from tests) and delete the client-side mapping entirely.
7. Move the approval gate server-side: `acknowledgedEdgeKeys` on `ReviewRequest` — today the
   "every static-only edge ticked" rule is browser-only, contradicting PRD 11.
8. One shared graph-traversal function to replace the 5 impact/lineage walk copies; add a result
   limit to `QueryService.impact` (the one unbounded impact surface).
9. Merge stage-ledger duplicates: B7 residue stub folded into B8; B1+B2/I1+I2; the
   `workflows/baseline.py` vs `incremental.py` checkpoint machinery (73/271 lines differ).
10. Deployment policy exists twice (`workflows/deployment.py` vs `DeploymentStageUseCase`).
11. `services/runtime_policy.py` (1,232 LOC, imported only by its own test) — delete or wire.
12. Java production support lives in `tests/integration/floci/java_stage_overrides.py`
    (1,293 LOC) — production `CoverageStageUseCase._baseline` is hardcoded to `.py` files; the
    Java cell must move under `src/`.
13. Frontend: replace the hand-rolled router (route table duplicated in `App.tsx` and
    `useParams`; query strings invisible) with react-router or a single route table.
14. `.worktrees/lineage-prototype/` stale ~35k-LOC duplicate tree — confirm with the owner and
    remove; add ESLint + `noUnusedLocals` to `apps/web` so dead-code rot is visible in CI.
15. Approval-id digests differ between SQLite and AWS paths for the same decision; unify the
    canonical identity.
