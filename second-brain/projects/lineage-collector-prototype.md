---
type: Project
title: Lineage Collector Prototype
description: A locally runnable M1 walking skeleton that carries one signed repository push through to a published, queryable column-level lineage graph.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype
tags: [lineage, prototype, walking-skeleton, fastapi, react]
timestamp: 2026-08-07T16:49:03Z
---

# Lineage Collector Prototype

The implementation of the Lineage Data Platform specs, living on branch
`codex/lineage-prototype` in a worktree — **not** on `main`. `main` holds only
specification: 18 component PRDs, an architecture deck, and design/plan docs.
Confusing the two wastes time; the code is in `.worktrees/lineage-prototype/`.

## What it proves

One signed push travels the full trust boundary end to end:

```
signed push → intake (HMAC, dedupe) → classify → SCA (AST walk)
  → resolve to URNs → consolidate (confidence band) → human approval
  → fenced publish (pointer swap) → version-pinned lineage/impact query
```

Deliberately local: FastAPI + SQLite + a write-once object directory on the
backend, React + TypeScript + Vite on the frontend. No AWS credentials, no LLM
key. Local stand-ins are one-to-one with the production mapping — SQLite
transactions for DynamoDB conditional writes, an append-only directory for S3
Object Lock, a versioned edge table for Neptune namespaces.

## State as of 2026-08-07

Genuinely implemented: URN resolver, intake and lanes, orchestration
(Incremental only), SCA engine, consolidation, evidence store, proposals and
review, fenced publication, query/impact APIs, repository classification, and a
four-workspace SPA.

Not implemented despite appearing in the type system: the LLM mechanism, real
runtime observation, the Baseline and Nightly workflows, and any AWS
infrastructure. See [LLM mechanism is modelled but not implemented](/notes/llm-mechanism-modelled-not-implemented.md)
for why this misleads readers of the confidence code.

## Architectural spine

Two rules generate most of the design. The publisher is the **only** writer to
the graph — consolidation never touches it. And reads resolve through a
**pointer** row, never against a table directly, which is what makes
publication atomic and rollback a pointer event rather than a data rewrite.

## Related

* [Confidence is agreement, not a score](/decisions/confidence-is-agreement-not-a-score.md) - the single idea that most shapes how this system behaves
* [Publication is a pointer swap, not an in-place write](/decisions/pointer-swap-over-in-place-write.md) - the other load-bearing decision
* [Running the lineage prototype locally](/playbooks/running-the-prototype-locally.md) - how to get it up and verify it actually works
* [The resumable publisher has no caller](/notes/resumable-publish-has-no-caller.md) - the most significant open gap found while exploring it

## Citations

1. [README.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/README.md)
2. [docs/prototype-coverage.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/prototype-coverage.md)
3. [docs/plans/2026-08-04-lineage-collector-prototype-design.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/plans/2026-08-04-lineage-collector-prototype-design.md)
