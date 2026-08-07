---
type: Reference
title: URN grammar and the quarantine rule
description: Every dataset and element is addressed by a canonical URN, and an ambiguous name is quarantined rather than resolved by similarity because a guessed URN is a defect.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/domain/urns.py
tags: [urn, resolver, identity, quarantine]
timestamp: 2026-08-07T16:49:03Z
---

# URN grammar and the quarantine rule

Every dataset and element in the platform has one canonical address:

```
urn:ldp:{env}:{platform}:{system}:{dataset}#{element}
urn:ldp:staging:snowflake:payments:raw.transactions#amount
```

All three evidence mechanisms resolve raw names through the **same** resolver
against a **pinned** catalog snapshot, which is what makes merging by URN
meaningful. If SCA and runtime resolved names differently, agreement between
them would be an accident.

## The rule that matters

**A guessed URN is a defect.** Ambiguous or unmatched names are quarantined —
never resolved by similarity, never by nearest match.

The reasoning is asymmetric, and worth internalising: a *missing* edge is a gap
someone will notice and can fill. A *wrongly resolved* edge is confidently
wrong, propagates into confidence bands, and may end up blocking a build over a
dependency that doesn't exist. The failure modes are not symmetric, so the
resolver refuses to trade one for the other.

Quarantined assertions never reach the consolidation ledger at all. They surface
in a triage queue for a human.

## Where names come from and how they fail

The resolver handles JDBC/URL parsing, credential stripping, config
substitution, and alias/exact/heuristic matching. What it cannot resolve becomes
**residue** — recorded explicitly with a reason code (dynamic name, reflection,
unparseable type) rather than silently dropped.

In the seeded fixture, `pipeline.py` deliberately contains a dynamic name
(`f"tenant_{tenant}.transactions"`) precisely to exercise this path. Note that
it lands as SCA *residue*, which is distinct from a quarantine record — the
`/api/quarantine` endpoint stays empty in the standard demo run. Two different
mechanisms for two different kinds of unknown.

## Related

* [Confidence is agreement, not a score](/decisions/confidence-is-agreement-not-a-score.md) - agreement is only meaningful because all mechanisms resolve identically
* [LLM mechanism is modelled but not implemented](/notes/llm-mechanism-modelled-not-implemented.md) - residue is what the LLM tier was designed to consume

## Citations

1. [domain/urns.py](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/domain/urns.py)
2. [services/resolver.py](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/resolver.py)
3. [component-prds/01-urn-and-resolver-library.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/component-prds/01-urn-and-resolver-library.md)
