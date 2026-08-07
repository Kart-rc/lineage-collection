---
type: Playbook
title: Running the lineage prototype locally
description: Start the API and SPA, then drive the full push-to-publish loop with curl to prove the trust boundary actually works end to end.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/README.md
tags: [playbook, local-dev, verification]
timestamp: 2026-08-07T16:49:03Z
---

# Running the lineage prototype locally

All commands verified on 2026-08-07 (macOS, Python 3.13, Node 24, `uv`).
Run from the worktree root, **not** the repo root — the code is not on `main`.

## Start

```bash
cd .worktrees/lineage-prototype
make setup      # uv sync + npm ci; safe to re-run
make dev        # concurrently runs uvicorn :8000 and vite :5173
```

UI at `http://127.0.0.1:5173`, API at `http://127.0.0.1:8000`, OpenAPI browser at
`/docs`.

Wait for readiness without blocking on a fixed sleep:

```bash
curl -s --retry 25 --retry-delay 1 --retry-all-errors --retry-connrefused \
  http://127.0.0.1:8000/healthz
```

## Drive the whole loop

```bash
# 1. reset — returns a pre-signed demo delivery
curl -s -X POST http://127.0.0.1:8000/api/demo/reset -o r.json
python3 -c "import json;d=json.load(open('r.json'));json.dump(
  {'payload':d['demoDelivery']['payload'],'signature':d['demoDelivery']['signature']},
  open('p.json','w'))"

# 2. signed push → 202, creates a run and a proposal IN_REVIEW
curl -s -X POST http://127.0.0.1:8000/api/events/push \
  -H 'content-type: application/json' -d @p.json

# 3. approve → publishes; pointer advances v1 → v2, fencing token 0 → 1
curl -s -X POST "http://127.0.0.1:8000/api/proposals/$PID/approve" \
  -H 'content-type: application/json' \
  -d '{"version":1,"actor":"you","rationale":"Verified.","expectedLockVersion":1}'

# 4. lineage, pinned to the published namespace
curl -s "http://127.0.0.1:8000/api/lineage/urn%3Aldp%3Astaging%3Asnowflake%3Apayments%3Araw.transactions%23amount?direction=down&depth=3"

# 5. impact
curl -s -X POST http://127.0.0.1:8000/api/impact -H 'content-type: application/json' \
  -d '{"subject":"urn:ldp:staging:snowflake:payments:raw.transactions#amount","changeType":"COLUMN_DROP","depth":5}'
```

Note the URN must be percent-encoded in the path: `:` → `%3A`, `#` → `%23`.

## What a healthy run looks like

| Step | Expected |
|---|---|
| push | `202 ACCEPTED`, 3 edges, all `HIGH` / `ELEMENT`, provenance `[SCA, RUNTIME]` |
| approve | `200`, proposal `FINALIZED`, pointer `v2`, token `1` |
| lineage | `namespaceVersion: v2`, 1 edge at depth 3 |
| impact | `{"block": 1, "warn": 0, "info": 0}` |

## Two invariants worth asserting

```bash
# replaying the same delivery must dedupe, not double-run
→ 200 with outcome DUPLICATE

# replaying the same approval must hit the optimistic lock
→ 409 with code CONCURRENT_DECISION
```

If either returns something else, something is genuinely wrong.

## Gotchas

* `make dev` uses `--reload`; if another process is editing `apps/api/`, uvicorn
  restarts mid-request and results get confusing. Check `git status` first.
* A 500 on `/api/overview` or `approve` usually means a stale dev database —
  see [Rebuilding a stale dev database](/playbooks/rebuilding-a-stale-dev-database.md).
* `make verify` (`npm test && npm run build`) is the pre-handoff gate.

## Related

* [Rebuilding a stale dev database](/playbooks/rebuilding-a-stale-dev-database.md) - the fix when the app 500s on startup-adjacent endpoints
* [Lineage Collector Prototype](/projects/lineage-collector-prototype.md) - what this system is and what it does not yet do
* [Impact severity gating](/references/impact-severity-gating.md) - why step 5 returns BLOCK rather than WARN

## Citations

1. [README.md](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/README.md)
2. [Makefile](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/Makefile)
