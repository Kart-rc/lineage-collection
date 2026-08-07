---
type: Reference
title: Fenced publication protocol
description: A monotonic fencing token reserved up front lets a stalled publisher detect it has been superseded and abort before touching the pointer.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py
tags: [publication, concurrency, fencing, distributed-systems]
timestamp: 2026-08-07T16:49:03Z
---

# Fenced publication protocol

The problem: a publisher can stall — GC pause, slow disk, paused container — and
wake up believing it still owns the environment. If it then flips the pointer,
it overwrites whatever a newer publisher did in the meantime.

The fix is a **fencing token**: a monotonically increasing integer reserved at
the start of the operation and re-checked immediately before the only
destructive act.

## The sequence

1. **Reserve** — read the current pointer, confirm it still matches the
   proposal's expected base, compute `token = max(pointer.fencing_token,
   prior_reservation.token) + 1`, and upsert it into `publish_reservations`.
   The namespace version is derived as `v{token + 1}`.
2. **Stage** — write the manifest, create the namespace, write the edges. All of
   this is invisible to readers.
3. **Verify** — re-read the staged rows, recompute the checksum, compare against
   the manifest's count and checksum.
4. **Activate** — check the fence, then swap.

## The fence check

```python
if (
    current_reservation is None
    or int(current_reservation["token"]) != reservation.token
    or current_reservation["status"] != "RESERVED"
):
    raise DomainError("FENCE_LOST", "A newer publisher owns the activation fence", ...)
```

Because tokens are reserved up front and monotonic, this reduces to an integer
comparison inside a transaction — no clocks, no leases to renew, no consensus.

What makes it airtight: the fence check, the pointer check, the verification and
the pointer swap all happen in **one transaction**. Verification and cutover
cannot diverge, because they commit together.

## Where a superseded publisher stops

At the fence check in `activate()` — *before* the pointer read, before
verification, before any write. It may already have written edge rows during
staging. Those are harmless: they live in a namespace version no pointer
references. Garbage, not corruption.

## Failure codes

| Code | Meaning |
|---|---|
| `POINTER_CONFLICT` | Active version no longer matches the proposal's base — rebase required |
| `FENCE_LOST` | A newer publisher reserved the environment while this one stalled |
| `VERIFY_MISMATCH` | Staged rows disagree with the manifest's count or checksum |

## Related

* [Publication is a pointer swap, not an in-place write](/decisions/pointer-swap-over-in-place-write.md) - the decision this protocol protects
* [Publication state machine boundaries](/references/publication-state-machine-boundaries.md) - observed state at each step of this sequence

## Citations

1. [services/publisher.py:167-240](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py) — `activate()` and the fence check
2. [services/publisher.py:365-414](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py) — `_reserve_operation()`
3. [component-prds/10-fenced-publication-and-projections.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/docs/component-prds/10-fenced-publication-and-projections.md)
