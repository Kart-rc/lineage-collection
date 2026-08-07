---
type: Note
title: The resumable publisher has no caller
description: The publication state machine resumes correctly from every crash boundary, but nothing in the running system ever re-drives it, so a mid-publish crash strands the operation permanently.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py
tags: [publication, resilience, gap, recovery]
timestamp: 2026-08-07T16:49:03Z
---

# The resumable publisher has no caller

Found on 2026-08-07 by crashing the real publisher at all six durable
boundaries via its own `set_fault_injector` hook.

**The machinery works.** Every crash point resumes to exactly `v2` / fencing
token `1`. A third call after completion is a genuine no-op. The state machine
is correct.

**But nothing calls it.** Retrying through the API's `approve()` endpoint fails
with `409 CONCURRENT_DECISION` — and correctly so. The review decision was
already committed by the crashed attempt, so the optimistic lock rejects the
retry. Resumption has to re-drive `publisher.publish()` directly with the
original proposal and approval object.

There is no sweeper, no recovery worker, no startup scan for stranded
operations. A crash mid-publish today leaves a row in `publication_operations`
that nobody will ever pick up.

## Why this is easy to miss

The happy path and the resume path are the *same code*, so every test passes and
the design reads as complete. The gap isn't in the state machine — it's in the
absence of anything that would ever invoke it a second time. You only see it by
crashing the thing and then asking "so who restarts it?"

## Status

Actively being closed. An in-flight `apps/api/tests/faults/test_publication_recovery.py`
drives `PublisherService` directly, which is the right entry point. The
`terminal_outcome` column on `publication_operations` (migration 7) exists to
record how an operation ended, which is what a sweeper would query on.

## Related

* [Publication state machine boundaries](/references/publication-state-machine-boundaries.md) - the crash/resume evidence this note is drawn from
* [Capturing a real publish trace](/playbooks/capturing-a-real-publish-trace.md) - how to reproduce the finding
* [Publication is a pointer swap, not an in-place write](/decisions/pointer-swap-over-in-place-write.md) - why a stranded operation is harmless rather than corrupting

## Citations

1. [services/publisher.py:242-296](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py) — `publish()` stage chain
2. [services/orchestration.py:1267-1280](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/orchestration.py) — `approve()` commits review before publishing
