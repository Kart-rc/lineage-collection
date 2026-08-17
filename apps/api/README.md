# `lineage-api` — collection engine and product API

The backend. It accepts signed repository events, runs deterministic lineage
collection over an exact Git revision, optionally corroborates what it found by
executing a generated harness, produces a reviewable proposal, publishes the result
behind a monotonic fence, and serves version-pinned lineage and impact queries.

FastAPI + SQLite + a write-once object directory locally. The same package also
ships nine AWS Lambda entry points, an SCA Fargate worker, and concrete DynamoDB /
S3 / SQS / Neptune / Kinesis adapters — one codebase, two deployment shapes.

---

## What it actually does

Lineage here is **evidence-first**. Nothing enters the graph without a citation, and
confidence is a function of how many independent mechanisms agree.

| Mechanism | Meaning | Where it comes from |
|---|---|---|
| `SCA` | Static code analysis | Reading committed Git blobs — never executing the repo |
| `RUNTIME` | Execution witnessed the claim | A generated harness, opt-in |
| `LLM` | Guardrailed inference | Not wired in this build |

Those combine into a confidence band by a fixed rule (`domain/confidence.py`):
`{SCA}` alone is `SINGLE`, `{SCA, RUNTIME}` is `HIGH`, all three is `HIGHEST`. There
is no scoring model and no tuning — the band is a pure function of the mechanism set,
so the same evidence always yields the same number.

### The five workflows

Declared in one authority file, `application/workflows/definitions.py`, which also
generates the Step Functions ASL under `infra/workflows/`. Never hand-edit the ASL.

| Workflow | Stages | Purpose |
|---|---|---|
| Baseline | B1–B10 | First full collection for a repository |
| Incremental | I1–I10 | Re-collection after a change |
| PR Gate | P1–P8 | Bounded, read-only impact check on a pull request |
| Deployment | D1–D6 | Promote an already-published graph by exact artifact digest |
| Nightly | N1–N6 | Reconciliation, drift sampling, manifest audit |

Which one runs is decided entirely by the event's `eventType` (`services/intake.py`).

> **Worth knowing:** every product path — the CLI, `POST /api/collections`, and the
> demo — currently emits `repo.push`, so they all run **Incremental**. Baseline is
> implemented and tested but has no live trigger yet.

---

## Layout

```
src/lineage_api/
├── domain/           pure rules — confidence bands, URNs, proposal states, impact severity
├── application/      workflow definitions, stage handlers, collection orchestration
├── services/         analyzers, resolver, consolidation, review, publisher, orchestration
├── infrastructure/   adapters — SQLite, local/remote Git, and the aws/ subpackage
├── entrypoints/      AWS Lambda handlers and the SCA worker
├── runtime/          SDK, producer, transport and OTel/OpenLineage adapters for
│                     evidence pushed in by an external producer
├── main.py           FastAPI application and routes
├── cli.py            operator commands: collect-checkout, worker, reset
├── config.py         environment resolution — fails closed on secrets
└── migrations.py     versioned schema
```

The dependency rule is inward-only: `domain` knows nothing about the layers above it,
and `infrastructure` is reached through the ports in `application/ports.py`.

---

## Running it

### Prerequisites

Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/). No AWS credentials and no
LLM key are needed for local use.

### Install

```bash
uv sync --project apps/api --extra dev
```

### Serve

From the repository root, the normal path is `make dev`, which runs this API and the
web UI together. To run only the API:

```bash
LINEAGE_DEV_MODE=1 uv run --project apps/api \
  uvicorn lineage_api.main:app --reload --host 127.0.0.1 --port 8000
```

- Health: <http://127.0.0.1:8000/healthz>
- Interactive API docs: <http://127.0.0.1:8000/docs>

### Collect a real repository

```bash
export LINEAGE_DATA_DIR=/tmp/lineage-state
export LINEAGE_WEBHOOK_SECRET=a-private-value-at-least-16-bytes

uv run --project apps/api python -m lineage_api.cli collect-checkout \
  --checkout /absolute/path/to/spring-service \
  --origin https://github.com/acme/spring-service \
  --revision <exact-40-character-commit> \
  --repository spring-service \
  --environment staging \
  --platform postgres \
  --system orders \
  --analyzer-pack java-spring-data-jpa-v1 \
  --ruleset spring-data-rules-v1 \
  --profile postgres
```

Add `--runtime-verification` to execute the corroboration harness. It is off by
default; see [Runtime verification](#runtime-verification).

Collection stops at `IN_REVIEW`. It never approves or publishes on its own.

### Drain durable work without the web process

```bash
uv run --project apps/api python -m lineage_api.cli worker --drain --max-messages 100
```

Use `--once` to process at most one available command.

### Test

```bash
uv run --project apps/api --extra dev pytest -q
```

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LINEAGE_WEBHOOK_SECRET` | **required** | HMAC key for every signed delivery |
| `LINEAGE_DEV_MODE` | `0` | Permits the published demo secret. Local development only |
| `LINEAGE_API_TOKEN` | unset | When set, every route but `/healthz` requires `Authorization: Bearer <token>` |
| `LINEAGE_DATA_DIR` | `./data` | SQLite database and write-once object directory |
| `LINEAGE_ALLOW_LOCAL_REPOSITORY_SOURCES` | `false` | Permit `LOCAL_CHECKOUT` sources |
| `LINEAGE_REPOSITORY_CLONE_TIMEOUT_SECONDS` | `60` | Bounded, 1–600 |
| `LINEAGE_REPOSITORY_GIT_OUTPUT_LIMIT_BYTES` | `65536` | Bounded, 1–1048576 |
| `LINEAGE_JAVA_HOME` | unset | JDK for the runtime harness; falls back to `javac` on `PATH` |

### Security posture

`LINEAGE_WEBHOOK_SECRET` authenticates **every** signed input — push deliveries,
deployment outcomes, and (via a derived key) runtime observations. Resolution fails
closed: a missing secret, the published demo secret, or anything under 16 bytes is
refused outside development mode, rather than silently falling back to a value anyone
who can read this repository already knows.

`LINEAGE_API_TOKEN` is unset by default, which leaves the API open — correct for a
single-operator local run, and what the demo walkthrough expects. Set it for anything
reachable by more than one person. It gates reads as well as writes, because the
lineage graph discloses the estate's schema and topology.

> The React UI does not yet send a bearer token, so setting `LINEAGE_API_TOKEN` while
> serving the UI from the same origin will make the UI receive `401`. Use the token
> for headless and deployed access; leave it unset for the local UI walkthrough.

---

## Runtime verification

Off by default. Enable per collection with `--runtime-verification` on the CLI, or
`"runtimeVerification": true` on `POST /api/collections`.

When enabled, the collector generates a small throwaway program **from that run's
static findings**, compiles it, runs it, reads what it observed, and deletes
everything. For Java it substitutes a recording proxy for the Spring Data repository
and calls the real injection site; for Python it binds recording implementations of
the dataset primitives the analyzed module declares but never defines.

There is no Spring context, no JDBC driver, no database, no network, and no build
tool. A runtime observation can only ever **corroborate** an edge static analysis
already proposed — it can never invent one, and a claim that goes unwitnessed keeps
its confidence rather than losing any.

Two constraints are deliberate and load-bearing:

- **Execution is opt-in.** `run_test_plan` refuses to run without
  `allow_execution=True`, and is intended for trusted sources only. Untrusted
  repository code belongs in an isolated worker, which this build does not yet have.
- **Production is refused.** `grant_session` rejects any environment named `prod*`
  with `RUNTIME_PRODUCTION_DENIED`. Production is meant to *inherit* verified lineage
  through exact-artifact promotion (D1–D6), not to produce it.

---

## API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Liveness. Never gated by the API token |
| `POST` | `/api/collections` | Submit a repository for collection |
| `GET` | `/api/collections/{commandId}` | Poll collection status |
| `POST` | `/api/events/push` | Signed repository event intake |
| `POST` | `/api/deployments/outcomes` | Signed deployment outcome |
| `GET` | `/api/overview` | Pointer, fence, counts, health |
| `GET` | `/api/operations/resilience` | Per-subsystem health rollup |
| `GET` | `/api/runs`, `/api/runs/{id}` | Run list and stage timeline |
| `GET` | `/api/proposals`, `/api/proposals/{id}` | Review queue and detail |
| `POST` | `/api/proposals/{id}/approve\|reject\|correct` | Review decisions |
| `GET` | `/api/lineage/{urn}` | Graph walk. `direction=up\|down`, `depth`, `version` |
| `POST` | `/api/impact` | Impact analysis for a proposed change |
| `POST` | `/api/pr-gate/evaluate` | Bounded read-only PR verdict |
| `GET` | `/api/edges/{edgeKey}` | Single edge with full provenance |
| `GET` | `/api/quarantine`, `/api/audit` | Rejected events and the decision log |
| `POST` | `/api/demo/reset` | Restore the seeded demo state |

Approving a proposal **publishes synchronously** in the same request — there is no
separate publish call. The publish itself is a resumable ten-stage saga, so a crash
mid-publish resumes at the last completed boundary.

---

## Durability

Work is at-least-once at the queue layer and exactly-once in its effects.

- **Commands** carry an idempotency key derived from the artifact and determinant
  digests. Re-submitting identical work returns the stored result rather than
  repeating it.
- **Leases** are fenced by a monotonic `lease_epoch`; an expired lease is force-closed
  before reclaim, so two workers cannot both complete an attempt.
- **Stage results** are immutable by key — replaying a completed stage with a
  different checksum raises `IdempotencyConflictError` instead of overwriting.
- **Evidence** is write-once twice over: `O_EXCL` on the filesystem plus a
  checksum-compared index row that raises `OVERWRITE_ATTEMPT`.
- **Publication** advances a monotonic fence; a stale publisher loses with
  `FENCE_LOST` rather than clobbering.

---

## Related

- [Repository overview and navigation](../../docs/NAVIGATION.md)
- [Web control room](../web/README.md)
- [Infrastructure](../../infra/README.md)
