---
type: Decision
title: Publication is a pointer swap, not an in-place write
description: Publishing writes a whole new immutable graph namespace beside the live one and flips a single pointer row, making the cutover atomic and rollback a pointer event.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py
tags: [publication, atomicity, design-decision, rollback]
timestamp: 2026-08-07T16:49:03Z
---

# Publication is a pointer swap, not an in-place write

Publishing never mutates the live graph. It writes a brand-new immutable
namespace (`v2`) alongside the live one (`v1`), verifies it against its
manifest, then updates one row in `pointers`.

Every read resolves through that pointer. Nothing reads a graph table directly.

## What this buys

**Atomicity without locking readers.** By the time the pointer moves there is no
data left to write, so the cutover is a single-row update inside a transaction.
Readers see v1, then they see v2. There is no boundary at which anyone observes
a partial graph.

**Rollback is a pointer event, never a data rewrite.** The prior namespace is
still sitting there marked `PRIOR`. Reverting is the same flip in reverse, which
means recovery doesn't depend on reconstructing anything.

**Failed publishes leave garbage, not corruption.** A publisher that dies
mid-stage leaves orphaned rows in a namespace version no pointer references.
They're invisible and harmless. This is the direct payoff of separating "write
the data" from "make the data visible."

## The counter-intuitive consequence

Staged edges are written to `graph_edges` **while still invisible**. Observed in
a real trace: all three v2 edges are committed three full steps before the
pointer moves. The database physically contains the new graph and no reader can
see it. Invisibility comes from the pointer, not from absence of data — which is
the single most common misreading of this design.

## Related

* [Fenced publication protocol](/references/fenced-publication-protocol.md) - the mechanism that makes concurrent publishers safe
* [Publication state machine boundaries](/references/publication-state-machine-boundaries.md) - the observed per-boundary state, including the invisible-write window
* [Lineage Collector Prototype](/projects/lineage-collector-prototype.md) - the system this decision structures

## Citations

1. [services/publisher.py:167-240](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py) — `activate()`
2. [component-prds/10-fenced-publication-and-projections.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/component-prds/10-fenced-publication-and-projections.md)
