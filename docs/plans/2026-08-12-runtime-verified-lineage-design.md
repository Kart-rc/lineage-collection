# Runtime-Verified Lineage — Design

| Field | Value |
|---|---|
| Status | Slice implemented; orchestration wiring outstanding |
| Implements | `apps/api/src/lineage_api/services/runtime_verification.py` |
| Evidence | `apps/api/tests/services/test_runtime_verification.py` (14 tests) |

## 1. The idea

As part of the *same* collection mechanism, the code the analyzer just read statically is
instrumented, test cases are generated from the static claim, those tests are run, and the
resulting runtime observations are used to verify the SCA-derived lineage.

```
repository snapshot ──► SCA ──► static edges
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            generated test plan            (existing) proposal
                    │
                    ▼
        instrumented run ──► observations ──► verification
                                                    │
                        corroborated / static-only / runtime-only
```

The direction of trust is one-way and deliberate. A generated test can only *target* an
edge SCA already claimed. Matching observations lift confidence. Missing ones are reported
as uncorroborated rather than dropped. Observations with no static counterpart are reported
as runtime-only and never become lineage — the same rule
`ConsolidationService.merge_runtime_observation` already enforces downstream:
*"Corroborate existing edges; runtime evidence never invents a new edge."*

## 2. Why most of this already existed

The platform already had the second half of the loop:

| Capability | Where |
|---|---|
| Session lifecycle: grant, ready, observe, drain, close, revoke | `services/runtime.py` |
| Closed observation schema and mechanism parsers (SDK, OpenLineage, OTel) | `services/runtime.py`, `packages/contracts/runtime-observation.schema.json` |
| Window/lease validation, counters, kill switch, production hard-deny | `application/runtime_validation.py` |
| Corroboration without invention, confidence bands | `services/consolidation.py` |

What was missing was the step that *produces* evidence for a repository the analyzer has
just read. Until now `runtimeStatus` was structurally always `NOT_PROVIDED` for a real
collection: nothing generated or ran anything.

## 3. What this slice implements

`services/runtime_verification.py`, in four parts:

1. **`generate_test_plan(module_path, source, static_edges)`** — parses the analyzed module
   and derives a bounded, deterministic call plan. Arguments are synthesized from
   *annotations*, never guessed from names, so the same source always yields the same plan.
   A signature that cannot be synthesized (unannotated, variadic, keyword-only) is skipped
   rather than invented; private functions are excluded.

2. **`RecordingInstrumentation`** — supplies the dataset primitives the analyzed module
   declares but never defines. This is the instrumentation seam: the same call sites SCA
   reads statically become the emission points at runtime. An element is only recorded when
   the transform actually references it, so an observation cannot claim a dependency the run
   never exercised.

3. **`run_test_plan(...)`** — executes each case with instrumentation bound, under a
   deadline. A case that raises is recorded *by exception class*, never by message, because
   a message can carry workload values.

4. **`verify_static_lineage(static_edges, observations, resolve)`** — resolves observed
   dataset/element pairs through an injected resolver (no guessing: unknown names resolve to
   `None` and become runtime-only) and partitions the result into corroborated, static-only
   and runtime-only, with a `CORROBORATED` / `PARTIALLY_CORROBORATED` / `NOT_PROVIDED`
   verdict.

Demonstrated end to end against the real `payments-pipeline` fixture: the plan is generated
from the module, the module actually executes under instrumentation, and all three static
edges are corroborated by observed execution.

## 4. Executing code is a privilege

`run_test_plan` refuses to run unless the caller passes `allow_execution=True`.

This is a genuinely different phase from static collection, which
`docs/remaining-goals.md` §7 forbids from executing repository code at all. The in-process
runner here is for **trusted, instrumented, non-production** verification. Running untrusted
third-party repository code belongs in the isolated Fargate worker the architecture already
reserves for it, with the network denied and the workspace destroyed — not in the API
process. That boundary is stated in the module docstring so it cannot be lost.

## 5. What remains to wire it in

This slice is a library, not yet a stage. To complete the loop in the product flow:

1. **A collection stage** that, after SCA proposes edges, generates and runs the plan inside
   the isolated worker and emits observations through the existing SDK producer rather than
   returning them in-process.
2. **Session binding** — obtain a runtime lease via `RuntimeLineageService.grant_session`,
   emit under it, and close the window so the existing validator and counters apply.
3. **Consolidation join** — feed accepted observations to `merge_runtime_observation` so the
   confidence band lifts from `SINGLE` to `HIGH` where SCA and runtime agree, and surface
   the verdict in the collection status document alongside `runtimeStatus`.
4. **Language reach** — the current instrumentation binds Python dataset primitives. The
   Java/Spring cell would need the equivalent seam (an agent or a test-scoped repository
   proxy), which is why the observation contract is mechanism-tagged rather than
   language-specific.

Until those land, `runtimeStatus` stays `NOT_PROVIDED` in the product flow and this module
is exercised only by its own tests. That is the honest status: the loop is proven to work,
but it is not yet on the collection path.
