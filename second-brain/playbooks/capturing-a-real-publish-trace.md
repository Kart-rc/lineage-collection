---
type: Playbook
title: Capturing a real publish trace
description: Use the publisher's own fault-injection seam to crash it at each durable boundary and record actual database state, without touching the live dev database.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py
tags: [playbook, debugging, fault-injection, evidence]
timestamp: 2026-08-07T16:49:03Z
---

# Capturing a real publish trace

`PublisherService` ships a test seam, `set_fault_injector(fn)`, which is called
after every durable boundary with the boundary's name. Raise from it and the
publisher dies exactly there, leaving real persisted state behind.

This is how you get evidence about crash behaviour instead of reasoning about it.

## Build an isolated stack

Never point this at `data/lineage.db`. Construct `Settings` over a temp
directory, exactly as the test suite does:

```python
settings = Settings(
    project_root=PROJECT_ROOT,
    data_directory=tmp,
    fixture_directory=PROJECT_ROOT / "fixtures",
    database_path=tmp / "lineage.db",
    object_directory=tmp / "objects",
    webhook_secret="local-lineage-demo-secret",
)
services = build_services(settings)
services.reset()
demo = services.demo_delivery()
services.orchestration.process_push(PushDelivery(demo["payload"], demo["signature"]))
```

`process_push` takes a `PushDelivery` object, not `(payload, signature)` — easy
to get wrong from the HTTP shape.

## Crash, then resume

Commit the review decision **once**, before crashing, then drive publication
separately. This mirrors production: the crash is in publication, not review.

```python
decision = services.review.approve(pid, version, actor, rationale, lock_version)

def injector(name):
    if name == boundary:
        raise InjectedCrash(name)

services.publisher.set_fault_injector(injector)
try:
    services.publisher.publish(decision.proposal, decision.approval, env="staging")
except InjectedCrash:
    pass
snapshot(db)                                   # real state, mid-flight

services.publisher.set_fault_injector(None)
services.publisher.publish(decision.proposal, decision.approval, env="staging")
snapshot(db)                                   # resumed to COMPLETED
```

## The trap that wasted time

Resuming by calling `orchestration.approve()` again fails with
`CONCURRENT_DECISION` — the review decision was already committed, so the
optimistic lock rejects the retry. Resume through `publisher.publish()` with the
original proposal and approval objects. This isn't a bug in the lock; it's a
finding about where recovery has to live.

## What to snapshot

Read these directly from SQLite at each boundary: `pointers`
(`active_version`, `fencing_token`), `publish_reservations` (`token`, `status`),
`publication_operations` (`stage`, `namespace_version`, `terminal_outcome`),
`graph_versions` (`version`, `state`), and a `COUNT(*)` of `graph_edges` grouped
by version. That last one is what reveals the invisible-write window.

## Boundaries available

`RESERVATION_ACQUIRED`, `MANIFEST_WRITTEN`, `NAMESPACE_CREATED`,
`EDGE_BATCH_WRITTEN:{n}`, `VERIFIED`, `POINTER_ACTIVATED`, `OUTBOX_DELIVERED`.

## Related

* [Publication state machine boundaries](/references/publication-state-machine-boundaries.md) - the table this procedure produces
* [The resumable publisher has no caller](/notes/resumable-publish-has-no-caller.md) - the finding this procedure surfaced
* [Fenced publication protocol](/references/fenced-publication-protocol.md) - what the reservation and token fields mean

## Citations

1. [services/publisher.py:83-85](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/services/publisher.py) — `set_fault_injector`
2. [tests/test_walking_skeleton.py:15-23](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/tests/test_walking_skeleton.py) — isolated `Settings` pattern
