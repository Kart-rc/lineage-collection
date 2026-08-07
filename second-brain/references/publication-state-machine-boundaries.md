---
type: Reference
title: Publication state machine boundaries
description: Observed database state at each of the nine durable boundaries of a publish, including the window where the new graph exists but is invisible.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py
tags: [publication, state-machine, resilience, evidence]
timestamp: 2026-08-07T16:49:03Z
---

# Publication state machine boundaries

Captured 2026-08-07 by running the real publisher against a throwaway database
with its `set_fault_injector` hook installed. Every value observed, not inferred.

## Why `publish()` is resumable

It's a run of `if` statements — **not** `elif` — each guarded on the operation's
persisted stage, each re-loading the operation from the database rather than
trusting a local variable. A fresh call whose operation already sits at
`VERIFIED` falls straight through the earlier blocks and picks up at activation.

**Resumption and first-run are the same code path.** There is no separate
recovery routine that can drift out of sync with the happy path.

## Observed uninterrupted publish

| Boundary | Operation stage | Pointer | Rows in `graph_edges` |
|---|---|---|---|
| START | — | v1 / token 0 | — |
| RESERVATION_ACQUIRED | `RESERVED` | v1 / 0 | — |
| MANIFEST_WRITTEN | `MANIFEST_WRITTEN` | v1 / 0 | — |
| NAMESPACE_CREATED | `NAMESPACE_CREATED` | v1 / 0 | — |
| EDGE_BATCH_WRITTEN:1 | `EDGES_WRITTEN` | v1 / 0 | **v2: 3** |
| VERIFIED | `VERIFIED` | v1 / 0 | v2: 3 |
| POINTER_ACTIVATED | `ACTIVATED` | **v2 / 1** | v2: 3 |
| OUTBOX_DELIVERED | `DELIVERY_CONFIRMED` | v2 / 1 | v2: 3 |
| RETURNED | `COMPLETED` | v2 / 1 | v2: 3 |

**The row that teaches the design is `EDGE_BATCH_WRITTEN:1`.** All three v2 edges
are committed while the pointer still reads v1. For three further steps the
database physically contains the new graph and no reader can see it. The
manifest is also written *before* any graph rows exist — the claim precedes the
data, so the data can be checked against it.

## Observed crash and resume

Each row is an independent run: crash at that boundary, then re-invoke
`publish()` with identical arguments.

| Crashed at | Stage left behind | Pointer after crash | After resume |
|---|---|---|---|
| RESERVATION_ACQUIRED | `RESERVED` | v1 | `COMPLETED`, v2 / 1 |
| MANIFEST_WRITTEN | `MANIFEST_WRITTEN` | v1 | `COMPLETED`, v2 / 1 |
| NAMESPACE_CREATED | `NAMESPACE_CREATED` | v1 | `COMPLETED`, v2 / 1 |
| VERIFIED | `VERIFIED` | v1 | `COMPLETED`, v2 / 1 |
| POINTER_ACTIVATED | `ACTIVATED` | **v2** | `COMPLETED`, v2 / 1 |
| OUTBOX_DELIVERED | `DELIVERY_CONFIRMED` | v2 | `COMPLETED`, v2 / 1 |

Every crash converges on the identical end state. A third call after completion
changes nothing and raises nothing — the `COMPLETED` early-return is a true
no-op.

The pointer column splits exactly at `POINTER_ACTIVATED`. There is no boundary
at which a reader sees a partial graph.

## The failure mode to guard against

Changing those `if`s to `elif`s would leave every happy-path test green while
silently destroying resumability: a resumed call entering at `VERIFIED` would
execute one block and return without activating. Same for caching `operation` in
a local instead of re-loading. Both are invisible to any test that never crashes
mid-publish — which is why a separate `tests/faults/` suite has to exist.

## Related

* [Fenced publication protocol](/references/fenced-publication-protocol.md) - the concurrency control operating across these boundaries
* [The resumable publisher has no caller](/notes/resumable-publish-has-no-caller.md) - this all works, and nothing invokes it
* [Capturing a real publish trace](/playbooks/capturing-a-real-publish-trace.md) - how to regenerate this table

## Citations

1. [services/publisher.py:242-296](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py) — the stage chain
2. [apps/api/tests/faults/test_publication_recovery.py](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/tests/faults/test_publication_recovery.py) — in-flight fault suite
