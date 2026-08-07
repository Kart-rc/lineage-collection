---
type: Playbook
title: Rebuilding a stale dev database
description: When an in-place edit to an already-applied migration leaves data/lineage.db missing a column, back it up and rebuild rather than hand-patching.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/migrations.py
tags: [playbook, migrations, sqlite, debugging]
timestamp: 2026-08-07T16:49:03Z
---

# Rebuilding a stale dev database

## The symptom

Endpoints that worked yesterday return `500`, and the server log shows:

```
sqlite3.OperationalError: no such column: <name>
```

## The cause

A migration was **edited in place after it had already been applied** to your
dev database. The runner records applied versions in `schema_migrations` and
skips anything already there — and the DDL uses `CREATE TABLE IF NOT EXISTS`, so
re-running is a no-op. The new column never appears.

This is a normal hazard while a migration is still being authored, and it only
bites developers whose database predates the edit. CI is unaffected because it
always starts empty, which is exactly why it goes unnoticed.

## Confirm it before acting

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('data/lineage.db')
print('applied:', [r[0] for r in c.execute(
    'select version from schema_migrations order by version')])
print('columns:', [r[1] for r in c.execute(
    'pragma table_info(<table>)')])
"
```

Diagnostic: the migration version **is** listed as applied, and the column named
in the error **is** in `migrations.py` but **not** in `pragma table_info`. That
combination means in-place edit, not a missing migration.

## The fix

All local state is regenerable, and `data/` is gitignored — but back up anyway,
it costs nothing:

```bash
mv data/lineage.db /tmp/lineage.db.backup-$(date +%H%M%S)
uv run --project apps/api python -m lineage_api.cli reset
```

Then verify the column exists before restarting:

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('data/lineage.db')
print([r[1] for r in c.execute('pragma table_info(<table>)')])
"
```

## Do not hand-patch with ALTER TABLE

Tempting and wrong. It makes your database a shape no other environment has,
and it hides the fact that anyone else with an older database still has the bug.
Rebuild, so your schema is exactly what a fresh checkout produces.

## Real instance

2026-08-07: `publication_operations.terminal_outcome` was added to migration 7
(`RESUMABLE_PUBLICATION_SQL`) after that migration had already run locally.
`/api/overview` and `POST /api/proposals/{id}/approve` both 500'd. Rebuild fixed
both; nothing else was wrong.

## Related

* [Running the lineage prototype locally](/playbooks/running-the-prototype-locally.md) - the loop to re-verify once the schema is rebuilt
* [The resumable publisher has no caller](/notes/resumable-publish-has-no-caller.md) - the feature whose in-flight migration caused this instance

## Citations

1. [apps/api/src/lineage_api/migrations.py:260-299](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/migrations.py)
2. [apps/api/src/lineage_api/cli.py](file:///Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype/apps/api/src/lineage_api/cli.py) — `reset`
