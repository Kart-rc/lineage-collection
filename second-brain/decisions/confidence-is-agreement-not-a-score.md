---
type: Decision
title: Confidence is agreement, not a score
description: An edge's confidence band is a pure function of which set of independent mechanisms asserted it, with no probability model anywhere in the system.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/domain/confidence.py
tags: [confidence, lineage, design-decision, evidence]
timestamp: 2026-08-07T16:49:03Z
---

# Confidence is agreement, not a score

Column-level lineage can't be derived reliably from any single source. Static
analysis misses anything computed dynamically. Runtime observation only sees
paths the tests exercised. An LLM will confidently invent an edge. So the
platform runs the mechanisms **independently** and treats their *agreement* as
the signal.

`derive_band()` is the whole model, in fifteen lines:

| Mechanisms that agree | Band |
|---|---|
| `{LLM}` alone | `LOWEST` |
| any one mechanism alone | `SINGLE` |
| `{SCA, RUNTIME}` | `HIGH` |
| all three | `HIGHEST` |
| anything else (e.g. `{SCA, LLM}`) | `MEDIUM` |

## Why the ordering isn't arbitrary

`{SCA, RUNTIME}` outranks `{SCA, LLM}`, though both are two-mechanism
agreements. The bands encode **independence of failure modes**, not a count of
votes. SCA and runtime fail for unrelated reasons, so their agreement is strong
evidence. The LLM is *prompted with SCA's own output*, so an `{SCA, LLM}`
agreement is partly self-confirming — the LLM may agree because it was shown
SCA's answer. Correlated evidence is worth less, and the ordering says so.

`{LLM}` alone is demoted *below* the ordinary single-mechanism band, which is
what makes the never-block guarantee possible downstream.

## Three properties worth keeping

1. **It takes a `set`, not a list.** Two SCA assertions for one edge cannot
   inflate confidence — self-corroboration is structurally impossible rather
   than merely discouraged.
2. **Unknown mechanisms raise, they don't degrade.** A new mechanism can't be
   quietly added and land in the `MEDIUM` catch-all.
3. **The `{LLM}` check sits above the `len == 1` check.** The ordering of the
   branches *is* the policy — reordering them silently changes what can block a
   build.

## Related

* [Ordinal bands over a numeric confidence score](/decisions/ordinal-bands-over-numeric-score.md) - why this is a band table rather than a float
* [Impact severity gating](/references/impact-severity-gating.md) - where the band stops being inert and starts blocking builds
* [LLM mechanism is modelled but not implemented](/notes/llm-mechanism-modelled-not-implemented.md) - this code handles three mechanisms; the deployment only produces two

## Citations

1. [domain/confidence.py:25-39](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/domain/confidence.py)
2. [component-prds/07-consolidation-and-confidence.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/component-prds/07-consolidation-and-confidence.md)
