---
type: Reference
title: Impact severity gating
description: A PR-blocking verdict requires both a destructive change type and a confidence band of HIGH or better, which structurally prevents an LLM-only edge from stopping a build.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/domain/impact.py
tags: [impact, pr-gate, confidence, severity]
timestamp: 2026-08-07T16:49:03Z
---

# Impact severity gating

A confidence band is inert until something is gated on it. That happens in
`severity_for()` — six lines that decide whether a proposed schema change warns
a developer or blocks their build.

```python
confidence = BAND_ORDER[band]
if change_type in DESTRUCTIVE_CHANGES and confidence >= BAND_ORDER["HIGH"]:
    return "BLOCK"
if confidence >= BAND_ORDER["MEDIUM"]:
    return "WARN"
return "INFO"
```

Two dimensions: how destructive the change is × how much we believe the edge.

| | Destructive (`COLUMN_DROP`, `COLUMN_TYPE_CHANGE`, `DATASET_REMOVAL`) | Non-destructive (`COLUMN_RENAME`, `TRANSFORM_CHANGE`, `FINGERPRINT_DRIFT`) |
|---|---|---|
| `HIGHEST` / `HIGH` | **BLOCK** | WARN |
| `MEDIUM` | WARN | WARN |
| `SINGLE` / `LOWEST` | INFO | INFO |

Verified against the real functions across all seven mechanism subsets.

## The guarantee this encodes

**An LLM-only edge can never block a build.** `{LLM}` yields `LOWEST`, and
`LOWEST` cannot reach `BLOCK` through any change type. The platform's most
speculative mechanism is structurally incapable of stopping a developer.

That property is enforced by an integer comparison rather than by policy
documentation elsewhere — which is exactly where you want a guarantee like this
to live. It cannot be forgotten during a refactor of the review UI.

## The case that surprises people

`{SCA, LLM}` on a `COLUMN_DROP` yields **WARN**, not BLOCK. A destructive change
on a *twice-asserted* edge still doesn't block, because one of the two
mechanisms was the LLM and the pair only reaches `MEDIUM`. Two votes are not
enough; it depends on *which* two.

## Related

* [Confidence is agreement, not a score](/decisions/confidence-is-agreement-not-a-score.md) - where the band being compared here comes from
* [Ordinal bands over a numeric confidence score](/decisions/ordinal-bands-over-numeric-score.md) - why this is an ordinal comparison rather than a threshold on a float
* [LLM mechanism is modelled but not implemented](/notes/llm-mechanism-modelled-not-implemented.md) - the never-block guarantee is currently untestable end to end

## Citations

1. [domain/impact.py:13-42](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/domain/impact.py)
2. [component-prds/11-apis-review-ui-and-pr-gate.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/component-prds/11-apis-review-ui-and-pr-gate.md) — §15 impact analysis spec
