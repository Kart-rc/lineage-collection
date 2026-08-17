# Simplification Tranche 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four confirmed cross-implementation drift defects and remove verified dead code/duplication in the lineage platform, without changing any other behavior.

**Architecture:** Behavior fixes are surgical (one expression each) and TDD'd. Everything else is verify-then-delete or move-and-reimport consolidation, guarded by the existing pytest (~1,000 tests) and vitest (~60 tests) suites.

**Tech Stack:** Python 3.12/FastAPI/SQLite (uv), React + TypeScript + Vite (vitest, TanStack Query).

**Spec:** `docs/superpowers/plans/2026-08-16-simplification-tranche-1-spec.md`

## Global Constraints

- Behavior changes limited to the spec's four confirmed defects; all other changes behavior-preserving.
- Verify-then-delete: fresh grep over `apps/` (excluding `.worktrees/`) proving zero external references before every deletion. Never touch `.worktrees/`.
- Backend gate: `uv run --project apps/api pytest apps/api/tests -q` passes after each backend task.
- Frontend gate: `npm test --workspace apps/web -- --run` passes after each frontend task; `npm run build --workspace apps/web` passes at tranche end.
- TDD for behavioral fixes (failing test first).
- No new dependencies. No changes under `infra/`, `scripts/`, `packages/contracts/`, `second-brain/`, `source-materials/`, root design-prototype dirs.
- Commit per task with a conventional-commit message ending in the Claude co-author trailer.

---

### Task 1: Backend behavioral fixes — PR-gate verdict precedence + publish env

**Files:**
- Modify: `apps/api/src/lineage_api/application/workflows/pr_gate.py:153`
- Modify: `apps/api/src/lineage_api/services/orchestration.py:1726`
- Test: `apps/api/tests/application/test_pr_gate_workflow.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `PRGateWorkflow.evaluate` verdict precedence BLOCK > WARN > PASS (later tasks don't depend on it).

- [ ] **Step 1: Write the failing test.** Read `apps/api/tests/application/test_pr_gate_workflow.py` first (esp. `test_pr_gate_blocks_only_calibrated_non_llm_violation` at line 154 and the builders/fakes it uses). Add, in the same style and reusing the same helpers:

```python
def test_pr_gate_block_wins_over_warn_reasons() -> None:
    # Impact summary contains BOTH warn > 0 (adds IMPACT_WARNING reason)
    # and block > 0 with non-LLM evidence (calibrated block).
    # The verdict must be BLOCK, not WARN: a warning must never mask a block.
    ...
    assert result["verdict"] == "BLOCK"
    assert "IMPACT_WARNING" in result["reasons"]
```

Build the request exactly like the existing block test but make the fake `impact_reader` return a summary with `{"warn": 1, "block": 1}` (or add a second change that produces the warn). Do not weaken any existing assertion.

- [ ] **Step 2: Run it, verify it fails.** `uv run --project apps/api pytest apps/api/tests/application/test_pr_gate_workflow.py -q` — the new test FAILS with verdict `"WARN"`.

- [ ] **Step 3: Fix the precedence.** In `apps/api/src/lineage_api/application/workflows/pr_gate.py` line 153 replace:

```python
verdict = "WARN" if reasons else ("BLOCK" if calibrated_block else "PASS")
```

with the production-gate precedence (mirrors `application/pr_gate_execution.py:415`):

```python
verdict = "BLOCK" if calibrated_block else ("WARN" if reasons else "PASS")
```

- [ ] **Step 4: Run the file's tests.** Same command as Step 2 — all pass. If an existing test asserted the inverted precedence (a WARN verdict in a calibrated-block scenario), that assertion encodes the bug: update it to expect BLOCK and note this in your report.

- [ ] **Step 5: Fix the publish env.** In `apps/api/src/lineage_api/services/orchestration.py` line 1726 replace:

```python
published = self._publisher.publish(decision.proposal, decision.approval, env="staging")
```

with:

```python
published = self._publisher.publish(decision.proposal, decision.approval, env=run["env"])
```

(`run` is fetched on line 1724; lines 1728-1731 already read `run["env"]` for the checksum lookup, so a non-staging run currently publishes to `staging` and then fails read-back.)

- [ ] **Step 6: Full backend suite.** `uv run --project apps/api pytest apps/api/tests -q` — all pass.

- [ ] **Step 7: Commit.**

```bash
git add apps/api
git commit -m "fix: PR-gate BLOCK verdict wins over WARN reasons; publish to the run's env"
```

---

### Task 2: Backend dead-code removal (verify-then-delete batch)

**Files:**
- Delete: `apps/api/src/lineage_api/application/coverage.py`, `apps/api/tests/application/test_coverage.py`
- Modify: `apps/api/src/lineage_api/application/workflows/baseline.py` (remove `plan_repository`, `BaselineFanoutExceeded`)
- Modify: `apps/api/src/lineage_api/application/workflows/incremental.py` (remove `load`)
- Modify: `apps/api/src/lineage_api/application/models.py` (remove `CoverageManifest`, `CoverageState`, `LineagePackage`)
- Modify: `apps/api/src/lineage_api/application/ports.py` (remove `CoveragePort`, `ProposalPort`, `PublicationPort`, `CatalogSnapshotPort`, `ReceiptPort`, `TelemetryPort`)
- Modify: `apps/api/src/lineage_api/application/stage_handlers.py:~2289` (remove the `("PR_GATE", "P4")` assignment that is overwritten by the loop a few lines below)
- Modify: `apps/api/src/lineage_api/domain/proposals.py` (remove the `"DRAFT"` entry from `TRANSITIONS`)
- Test: `apps/api/tests/application/test_baseline_workflow.py`, `apps/api/tests/domain/test_proposals.py`

**Interfaces:** none produced; purely subtractive.

- [ ] **Step 1: Verify each target is dead.** For each symbol/module above run a fresh grep, e.g.:

```bash
grep -rn "CoverageVerifier\|CoverageVerification\|CoverageIncompleteError" apps --include="*.py" | grep -v ".worktrees"
grep -rn "plan_repository\|BaselineFanoutExceeded" apps --include="*.py" | grep -v ".worktrees"
grep -rn "CoverageManifest\|CoverageState\|LineagePackage" apps --include="*.py" | grep -v ".worktrees"
grep -rn "CoveragePort\|ProposalPort\|PublicationPort\|CatalogSnapshotPort\|ReceiptPort\|TelemetryPort" apps --include="*.py" | grep -v ".worktrees"
grep -rn "\"DRAFT\"\|'DRAFT'" apps --include="*.py" --include="*.ts" --include="*.tsx" | grep -v ".worktrees"
```

A symbol is deletable only when every hit is the definition itself, the dead module, or a test being deleted/updated in this task. **If any other hit appears, keep that symbol, note it in your report, and continue with the rest.** Also check `IncrementalWorkflow.load` (`grep -rn "\.load(" apps/api --include="*.py"` and inspect hits).

- [ ] **Step 2: Delete.** Remove the dead module + its test file, the dead methods/classes/ports/dataclasses, the unreachable `stage_handlers` P4 assignment (read the surrounding ~15 lines first to confirm the overwrite loop), and the `"DRAFT": {"IN_REVIEW"}` entry in `TRANSITIONS`. Remove imports that become unused (including any of the deleted models/ports imported elsewhere purely for typing — re-grep after deleting).

- [ ] **Step 3: Update tests.** In `test_baseline_workflow.py` delete only the tests exercising `plan_repository`/`BaselineFanoutExceeded`. In `test_proposals.py` update/remove the DRAFT-transition test (a transition from an unknown state must now raise `ValueError` via `allowed_transitions` — assert that instead if the test structure invites it).

- [ ] **Step 4: Full backend suite.** `uv run --project apps/api pytest apps/api/tests -q` — all pass.

- [ ] **Step 5: Commit.**

```bash
git add apps/api
git commit -m "refactor: delete dead coverage module, unused ports/models, unreachable DRAFT state"
```

---

### Task 3: Frontend confidence truth — full band projection + one runtime-verified predicate

**Files:**
- Modify: `apps/web/src/components/review/reviewMeta.ts`
- Modify: `apps/web/src/components/lineage/EdgeInspector.tsx` (lines ~7, ~204-209, ~310)
- Modify: `apps/web/src/components/lineage/lineageLayout.ts` (line ~19 `VERIFIED_BANDS`)
- Modify: `apps/web/src/components/lineage/LineageCanvas.tsx` (line ~24)
- Modify: `apps/web/src/pages/RunComparePage.tsx` (`evidenceClass` at ~53)
- Modify: whichever stylesheet defines the band-pill tone classes (find `verified`/`probable` tone classes; add an `inferred` variant with muted styling consistent with tokens.css)
- Test: existing vitest suites + a new/extended unit test for `bandDisplay`

**Interfaces:**
- Produces: `bandDisplay(band): { label: string; tone: "verified" | "probable" | "inferred"; percent: number }` and `isRuntimeVerified(edge)` as the single verification predicate. Task 4/5 must not re-introduce duplicates.

- [ ] **Step 1: Failing unit test for `bandDisplay`.** Create/extend a test (colocate with existing test conventions — look at `apps/web/src/**/*.test.ts*` and `src/test/`):

```ts
import { bandDisplay } from "../components/review/reviewMeta";

it("projects every backend band, including LOWEST as INFERRED", () => {
  expect(bandDisplay("HIGHEST")).toEqual({ label: "VERIFIED 96", tone: "verified", percent: 96 });
  expect(bandDisplay("HIGH")).toEqual({ label: "VERIFIED 92", tone: "verified", percent: 92 });
  expect(bandDisplay("MEDIUM")).toEqual({ label: "PROBABLE 78", tone: "probable", percent: 78 });
  expect(bandDisplay("SINGLE")).toEqual({ label: "PROBABLE 70", tone: "probable", percent: 70 });
  expect(bandDisplay("LOWEST")).toEqual({ label: "INFERRED 55", tone: "inferred", percent: 55 });
});
```

Run `npm test --workspace apps/web -- --run` — new test FAILS.

- [ ] **Step 2: Implement `bandDisplay`** in `reviewMeta.ts` (mirrors `apps/api/src/lineage_api/domain/product_confidence.py`; percents are uncalibrated ordinal markers):

```ts
export function bandDisplay(band: ConfidenceBand | string): {
  label: string;
  tone: "verified" | "probable" | "inferred";
  percent: number;
} {
  switch (band) {
    case "HIGHEST":
      return { label: "VERIFIED 96", tone: "verified", percent: 96 };
    case "HIGH":
      return { label: "VERIFIED 92", tone: "verified", percent: 92 };
    case "MEDIUM":
      return { label: "PROBABLE 78", tone: "probable", percent: 78 };
    case "SINGLE":
      return { label: "PROBABLE 70", tone: "probable", percent: 70 };
    default: // LOWEST and anything unknown projects to the weakest tier
      return { label: "INFERRED 55", tone: "inferred", percent: 55 };
  }
}
```

Fix every consumer the compiler or grep flags: callers that regex the number out of `label` (`EdgeInspector.tsx:~310`, `LineageCanvas.tsx:~24`) switch to `.percent`; the hardcoded `"VERIFIED 92"`/`"PROBABLE 70"` legend strings in `EdgeInspector.tsx:~204-209` switch to `bandDisplay("HIGH").label` / `bandDisplay("SINGLE").label`. Add the `inferred` tone class next to the existing `verified`/`probable` tone classes in CSS (grep for how `tone` becomes a class name).

- [ ] **Step 3: Unify the runtime-verified predicate.** Keep `isRuntimeVerified` in `reviewMeta.ts` as the single definition (band HIGH/HIGHEST OR any RUNTIME provenance). Replace the band-only sets `RUNTIME_BANDS` (`EdgeInspector.tsx:~7`) and `VERIFIED_BANDS` (`lineageLayout.ts:~19`) with imports of `isRuntimeVerified` where the call site has a full `LineageEdge`; if a site only has a band string (no provenance), export `const VERIFIED_BANDS: ReadonlySet<string>` from `reviewMeta.ts` and import it there instead of a local copy. In `RunComparePage.tsx` `evidenceClass`, use `isRuntimeVerified(edge)` for the verified branch, keeping its LLM class logic. Note in your report any visual behavior change this causes (canvas edges with RUNTIME provenance but band < HIGH now render verified — that is the intended unification).

- [ ] **Step 4: Run the suite.** `npm test --workspace apps/web -- --run` — all pass (update any test that asserted the old drifted labels; note each in the report).

- [ ] **Step 5: Commit.**

```bash
git add apps/web
git commit -m "fix: align confidence projection with backend (INFERRED 55) and unify runtime-verified predicate"
```

---

### Task 4: Frontend shared vocabulary — `lib/format.ts`, `lib/runs.ts`, `useProposalEdges`

**Files:**
- Create: `apps/web/src/lib/format.ts`, `apps/web/src/lib/runs.ts`, `apps/web/src/hooks/useProposalEdges.ts`
- Modify: `apps/web/src/pages/RunsPage.tsx`, `RunComparePage.tsx`, `ProposalDetailPage.tsx`, `OperationsPage.tsx`, `ReviewQueuePage.tsx`, `apps/web/src/components/runs/StageTimeline.tsx`, `apps/web/src/components/operations/CollectionStatus.tsx`, `apps/web/src/components/lineage/EdgeInspector.tsx`

**Interfaces:**
- Produces:
  - `lib/format.ts`: `shortDigest(value: string, length?: number): string`, `formatDurationMs(ms: number): string`, `formatDurationSeconds(seconds: number): string`, `statePillClass(state: string): string`.
  - `lib/runs.ts`: the run-state vocabulary, `stageTone`, `stageNumber`, `stagePipeline`, `PHASE_RANGES`, `runStatusTone` — everything `RunComparePage` currently imports from `RunsPage` plus the duplicated helpers.
  - `hooks/useProposalEdges.ts`: `useProposalEdges(proposal: Proposal | undefined): { edges: LineageEdge[]; missing: number; hydrationComplete: boolean }` (match the exact shape both pages currently derive — read both call sites first and preserve their query key `["proposal-edges", id, version]` and `staleTime: Infinity`).

- [ ] **Step 1: Inventory the duplicates.** Read the five digest shorteners (`RunsPage.tsx:45`, `StageTimeline.tsx:11`, `ProposalDetailPage.tsx:14`, `EdgeInspector.tsx:16`, `OperationsPage.tsx:31`), three duration formatters (`OperationsPage.tsx:24`, `CollectionStatus.tsx:32`, `RunsPage.tsx:63`), two `statePillClass` (`ReviewQueuePage.tsx:26`, `ProposalDetailPage.tsx:19`), two `stageTone` (`StageTimeline.tsx:4`, `RunsPage.tsx:240`), `stageNumber` (`RunsPage.tsx:138`, and in `StageRail.tsx`). Confirm parameter conventions (some shorten to different lengths — keep a `length` parameter defaulting to the dominant behavior and pass explicit lengths where call sites differ; output must be pixel-identical per call site).

- [ ] **Step 2: Create `lib/format.ts` and `lib/runs.ts`**, moving (not rewriting) the canonical implementations; update all call sites to import from the libs; delete the local copies. `RunComparePage` must no longer import anything from `RunsPage`.

- [ ] **Step 3: Extract `useProposalEdges`.** Read `ProposalDetailPage.tsx:96-157` and `RunComparePage.tsx:160-187`; extract the identical id-concat + batched `/edges/{key}` hydration + `missing` computation into the hook; both pages consume it. No query-key change.

- [ ] **Step 4: Suite + build.** `npm test --workspace apps/web -- --run` and `npm run build --workspace apps/web` — both pass, zero rendering diffs expected.

- [ ] **Step 5: Commit.**

```bash
git add apps/web
git commit -m "refactor: consolidate format/run-state helpers and proposal-edge hydration into shared modules"
```

---

### Task 5: Frontend dead-code removal (verify-then-delete)

**Files:**
- Modify: `apps/web/src/styles/global.css` (dead regions ~486-591, 615-756, 846-1078, 1189-1267, 1348-1587; `button--approve`, `button--danger`), `src/styles/pages/lineage.css`, `src/styles/pages/runs.css` (`runs-sr`, `runs-inline-note`)
- Modify: `apps/web/src/components/operations/StageRail.tsx` (keep only `runStatusTone` — or fold it into `lib/runs.ts` from Task 4 and delete the file)
- Modify: `apps/web/src/components/shared/StatusPill.tsx` (drop the never-passed `variant` prop + its CSS)
- Modify: `apps/web/src/components/lineage/LineageCanvas.tsx` (drop the `nodeTitle` re-export), `apps/web/src/pages/RunComparePage.tsx` (drop unused `formatWhen` import if still present after Task 4), `apps/web/src/pages/ReviewQueuePage.tsx:124-126` (remove the pagination note that has no load-more control)

**Interfaces:** consumes Task 4's `lib/runs.ts` (new home for `runStatusTone` if the file is deleted).

- [ ] **Step 1: Verify every CSS class before deleting.** For each class in a candidate region:

```bash
grep -rn "class-name-stem" apps/web/src --include="*.tsx" --include="*.ts"
```

Account for dynamic construction (`` `foo--${x}` ``): grep the stem before `--` as well. Delete only classes with zero hits. Work region by region; if a region turns out partially live, delete only the dead classes in it.

- [ ] **Step 2: TS dead code.** Grep-verify then remove: `StageRail` component/props/`isTerminalStatus`/private `stageNumber` (keep `runStatusTone` available at its Task-4 home; update the 4 importers), `StatusPill` `variant` + `status-pill--outline`/`--solid` CSS, `nodeTitle` re-export, the ReviewQueuePage note.

- [ ] **Step 3: Suite + build.** `npm test --workspace apps/web -- --run` and `npm run build --workspace apps/web` — both pass.

- [ ] **Step 4: Commit.**

```bash
git add apps/web
git commit -m "refactor: remove dead CSS regions and unreferenced component code"
```
