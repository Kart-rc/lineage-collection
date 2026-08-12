# Runtime on the Collection Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote runtime verification from a library nothing calls into a collection stage, so `runtimeStatus` stops being structurally `NOT_PROVIDED`, and add the per-edge liveness the product prototype ranks by.

**Architecture:** `services/runtime_verification.py` and `services/java_runtime_verification.py` already generate, run, and verify. `RuntimeLineageService` already grants sessions, validates windows and leases, and `ConsolidationService.merge_runtime_observation` already corroborates without inventing. Nothing wires them together: the design doc states plainly that the module "is not yet on the collection path." This plan adds the stage and the liveness model that depends on it.

**Tech Stack:** Python 3.12, pytest, existing `lineage_api.services.runtime`, `runtime_verification`, `consolidation`.

## Global Constraints

- **Runtime never invents an edge.** The existing rule stands: an observation with no static counterpart is reported as runtime-only and never becomes lineage.
- **Runtime MAY demote.** This is the new, explicit policy decision this plan introduces: a static edge that a *complete* runtime session never observed is marked unobserved and its liveness drops. It is never deleted, and its band never falls below what static evidence alone justifies.
- **Demotion requires a complete session.** An aborted, revoked, or partial session proves nothing about liveness and must not lower anything.
- **Execution stays privileged.** `run_test_plan` keeps `allow_execution=True`; untrusted repository code belongs in the isolated Fargate worker, not the API process.
- **Production is hard-denied.** Unchanged — the kill switch and production deny remain independent of this stage.
- Test command prefix: `uv run --project apps/api python -m pytest`

---

## File Structure

| File | Responsibility |
|---|---|
| `apps/api/src/lineage_api/services/liveness.py` (create) | The liveness model: observation counts, last-seen, and the HOT/WARM/COLD/UNOBSERVED band. |
| `apps/api/src/lineage_api/application/runtime_stage.py` (create) | The collection stage: bind a session, run verification, emit observations, return a verdict. |
| `apps/api/tests/services/test_liveness.py` (create) | Liveness banding and the complete-session precondition. |
| `apps/api/tests/application/test_runtime_stage.py` (create) | The stage end to end against the `payments-pipeline` fixture. |

---

### Task 1: The liveness model

**Interfaces:**
- Produces:
  - `LIVENESS_BANDS = ("HOT", "WARM", "COLD", "UNOBSERVED")`
  - `@dataclass(frozen=True, slots=True) EdgeLiveness(edge_key: str, observations: int, last_observed: str | None, band: str)`
  - `derive_liveness(edge_key: str, observations: int, last_observed: str | None, session_complete: bool) -> EdgeLiveness`

Banding, matching the prototype's `HOT`/`WARM`/`COLD`/`DEAD?`:

| Condition | Band |
|---|---|
| ≥ 100 observations | `HOT` |
| 10–99 | `WARM` |
| 1–9 | `COLD` |
| 0 observations **and** session complete | `UNOBSERVED` |
| 0 observations and session incomplete | `UNOBSERVED` with `last_observed=None`, but callers must not demote — see Task 2 |

The prototype's `DEAD?` is deliberately renamed `UNOBSERVED`. "Dead" asserts the code never runs; all the platform can prove is that *this session* did not observe it. Naming it `DEAD` would be a claim the evidence does not support.

- [ ] **Step 1:** Write `apps/api/tests/services/test_liveness.py` covering each band boundary (0, 1, 9, 10, 99, 100), the complete/incomplete distinction, and rejection of a negative count.
- [ ] **Step 2:** Run it; confirm `ModuleNotFoundError`.
- [ ] **Step 3:** Implement `derive_liveness` as a pure function with the table above; raise `ValueError` on a negative count.
- [ ] **Step 4:** Run; confirm green.
- [ ] **Step 5:** `git commit -m "feat: derive per-edge liveness bands from runtime observations"`

---

### Task 2: Demotion policy

**Interfaces:**
- Produces: `apply_liveness(edge: ConsolidatedEdge, liveness: EdgeLiveness, session_complete: bool) -> ConsolidatedEdge`

Rules, each its own test:
1. A complete session with zero observations sets liveness `UNOBSERVED` and leaves `band` untouched — static proof is unaffected by not having been exercised.
2. A complete session with observations lifts `corroboration` exactly as `merge_runtime_observation` already does; this function must not double-count.
3. An **incomplete** session never changes anything.
4. Liveness is additive metadata on the edge; no existing field is overwritten.

- [ ] **Step 1:** Write `apps/api/tests/services/test_liveness_policy.py` with one test per rule, including a regression test that an incomplete session leaves the edge byte-identical.
- [ ] **Step 2–4:** Red, implement, green.
- [ ] **Step 5:** `git commit -m "feat: let a complete runtime session mark static edges unobserved"`

---

### Task 3: The collection stage

**Interfaces:**
- Produces: `run_runtime_stage(module_path, source, static_edges, session, *, allow_execution: bool) -> RuntimeStageResult`
- `RuntimeStageResult(verdict: str, corroborated: int, static_only: int, runtime_only: int, session_id: str)`

Sequence: grant a session via `RuntimeLineageService.grant_session` → `generate_test_plan` → `run_test_plan(allow_execution=True)` → `verify_static_lineage` → emit each observation through the SDK producer under the lease → close the window → feed accepted observations to `merge_runtime_observation`.

- [ ] **Step 1:** Write `apps/api/tests/application/test_runtime_stage.py` asserting, against the real `payments-pipeline` fixture, that the stage returns `CORROBORATED` with 3 corroborated edges and 0 runtime-only, and that the session is closed afterwards.
- [ ] **Step 2:** Run; confirm it fails.
- [ ] **Step 3:** Implement the stage, reusing existing services — no new verification logic.
- [ ] **Step 4:** Run the full suite.
- [ ] **Step 5:** `git commit -m "feat: run runtime verification as a collection stage"`

---

### Task 4: Surface the verdict

- [ ] Replace the hardcoded `runtimeStatus: NOT_PROVIDED` in the collection status document with the stage's verdict.
- [ ] Assert in a test that a collection which ran the stage reports `CORROBORATED`, and one that did not still reports `NOT_PROVIDED` — the honest default.
- [ ] Update `docs/prototype-coverage.md` L06 and the runtime design doc's §5, which currently states the loop is "not yet on the collection path."

---

## Final verification

- [ ] Full suite green
- [ ] `runtimeStatus` is `CORROBORATED` for a collection that ran the stage
- [ ] An unexercised static edge reports liveness `UNOBSERVED` with its band unchanged
