---
type: Decision
title: Ordinal bands over a numeric confidence score
description: Confidence is expressed as five ordered bands rather than a float, because a float invites averaging and averaging destroys the agreement semantics.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/component-prds/99-implementation-readiness-review.md
tags: [confidence, design-decision, mvp-scope]
timestamp: 2026-08-07T16:49:03Z
---

# Ordinal bands over a numeric confidence score

Recorded as decision **Q1** in the implementation readiness review: ordinal
bands only at MVP, no numeric score.

The bands are `LOWEST < SINGLE < MEDIUM < HIGH < HIGHEST`, and the only
operation performed on them is comparison — `BAND_ORDER[band] >= BAND_ORDER["HIGH"]`.

## The rationale

A float invites averaging, and averaging is exactly the operation that destroys
the semantics the bands exist to preserve. "Two independent mechanisms agreed"
is a *structural* claim about evidence. Reduce it to `0.82` and someone
downstream will average it with another `0.82`, or threshold it at `0.8`, and
the fact that the two mechanisms could fail together has been silently
discarded.

The band table is deliberately not a computation. It's a lookup that a human
can audit line by line and argue with.

## A separate axis, deliberately not folded in

`corroboration` (`NONE | DATASET | ELEMENT`) is tracked as its own field rather
than being mixed into the band. Dataset-level runtime evidence never raises a
band — it's recorded, and it's visible in the UI, but it isn't allowed to buy
confidence it didn't earn at element level. Two axes stay two axes.

## Related

* [Confidence is agreement, not a score](/decisions/confidence-is-agreement-not-a-score.md) - the band table this decision chose the representation for
* [Impact severity gating](/references/impact-severity-gating.md) - the only consumer that compares bands, and it compares ordinals

## Citations

1. [component-prds/99-implementation-readiness-review.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/component-prds/99-implementation-readiness-review.md) — decision Q1
2. [domain/impact.py:6-12](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/domain/impact.py) — `BAND_ORDER`
