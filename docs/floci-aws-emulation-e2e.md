# floci-emulated AWS end-to-end: baseline · incremental · deployment · impact

*Recorded 2026-08-15. Runs `floci-20260815T030121Z` and `floci-20260815T030205Z`
(clean-emulator reproducibility pair), spring-petclinic pinned at `88e37c15`.*

## What ran

[floci](https://github.com/floci-io/floci) (a LocalStack-compatible AWS emulator,
`floci/floci:latest`, port 4566) emulates the platform's AWS data plane. The
production stage handlers execute in-process; **every AWS side effect is real
emulator state**: DynamoDB stage leases, run ledger, fenced pointer transactions
and outbox rows; versioned immutable S3 evidence and packages; SQS FIFO lane
intake with exactly-once redelivery proof; Kinesis runtime observations; Step
Functions `StartExecution` name-idempotency at intake.

| Path | Stages | Terminal | Proof |
|---|---|---|---|
| Baseline | intake + B1–B10 (+B5A map aggregation) | `PUBLISHED` | 23 edges (18 reads · 5 writes), 8 element edges band HIGH → VERIFIED 92 %, runtime `CORROBORATED`, pointer fence 1 |
| Incremental (delta) | I1–I10 | `PUBLISHED` | same 23 edges re-proved on a changed `OwnerController.java`, fence 1 → 2 |
| Incremental (docs) | I1–I10 | `NO_LINEAGE_IMPACT` | `readme.md`-only change, empty recompute scope, no approval needed |
| Deployment | D1–D6 via the real Lambda handler | `PROMOTED` | exact approved package resolved by artifact digest, fenced swap → fence 3 |
| Impact (PR gate) | P1–P8 | `BLOCK` | `COLUMN_DROP` on `owners#last_name` → 1 HIGH-band downstream consumer |

The Java seams (coverage/SCA/consolidation/publication/PR-gate URN grammar) are
additive driver-side overrides in `tests/integration/floci/java_stage_overrides.py`;
band math, fencing, checksums and artifact contracts are the production code.
The graph projection runs on SQLite (`tests/integration/floci/sqlite_projection.py`)
because floci's Neptune is a TinkerPop Gremlin server without the `neptunedata`
openCypher API the production adapter speaks (probed: `POST /opencypher` →
`UnknownOperationException`).

## How to reproduce

```bash
uv sync --project apps/api --extra aws --extra dev
git clone https://github.com/spring-projects/spring-petclinic /tmp/spring-petclinic
git -C /tmp/spring-petclinic checkout 88e37c15cf6fc8490b01bc3e8e2c800cec1ac272
./scripts/floci/run_floci_e2e.sh          # clean floci -> provision+probe -> full suite
```

Evidence lands under `data/acceptance/floci-<stamp>/` (ledger scan, pointer,
edge set, per-edge confidence, S3 inventory, Kinesis observations, PR check,
deployment state, summary). `scripts/floci/provision_floci.py` ends with a
compatibility probe (versioned S3 + `IfNoneMatch`, DynamoDB transactions + GSI,
FIFO dedupe, Kinesis, Step Functions duplicate-name conflict) and aborts with a
written report if the emulator falls short.

## Three real adapter defects the emulator surfaced

floci enforces DynamoDB's validation the way real AWS does, which exposed
latent bugs in `infrastructure/aws/dynamodb_control.py` that in-memory fakes
never caught (all fixed, 1249 backend tests green):

1. `record_stage_and_run` used bare `system`/`output` in an UpdateExpression —
   both are DynamoDB **reserved words** (now aliased `#system`/`#output`).
2. `record_deployed_digest` had a condition referencing `:result` with **no such
   value bound** — unconditionally invalid; the condition now admits the
   AUTHORITATIVE/RECORDED states or an existing `result` attribute.
3. `complete_deployment` bound an **unused** `:recorded` value and used the
   reserved word `result` bare (both fixed).

## Visualizing the emulated state (API · UI · DynamoDB browser)

The production **product API** (`ProductApiService`, the same code the AWS Lambda
wraps) can serve the React control room directly from the floci state:

```bash
./scripts/floci/serve_floci_ui.sh     # against the RUNNING emulator's live state
```

| URL | What |
|---|---|
| http://127.0.0.1:5173 | Control room — Runs, **Review queue** (approve/reject with real fenced publish), Lineage explorer, Impact |
| http://127.0.0.1:8000/api/overview | Product API (floci-backed `AwsProductQueryProjection` + SQLite graph projection) |
| http://localhost:8001 | DynamoDB browser (`dynamodb-admin`) over `lineage-{control,ledger,proposal,pointer}` |

`scripts/floci/floci_product_api.py` also plays the DynamoDB-stream role: after a
UI approval it drains the `PROPOSAL_APPROVED` outbox and executes the real fenced
publication (verified: APPROVE → PUBLISHED → pointer fence advanced).
`scripts/floci/seed_pending_proposal.py` runs a fresh baseline to
`AWAITING_APPROVAL` (unique artifact digest) so the Review queue always has a
real IN_REVIEW proposal to exercise. Notes: `POST /api/collections` returns the
honest `501 COLLECTION_SUBMIT_NOT_CONFIGURED` (the Fargate acquisition stage
does not exist in this environment), and the impact endpoint is dataset-scoped
by the production identifier grammar.

**Dockerized stack** (`scripts/floci/compose.yaml` + `Dockerfile.api` /
`Dockerfile.web`, images verified end-to-end):

```bash
docker compose -f scripts/floci/compose.yaml up -d --build   # floci + ddb-admin + api + nginx-served UI
uv run --project apps/api python scripts/floci/provision_floci.py
./scripts/floci/run_floci_e2e.sh                             # populate, then browse
```

The compose stack brings its own fresh floci; use `serve_floci_ui.sh` instead to
attach to an already-running emulator's state. On Docker Desktop, mounting
`data/floci/` for the api service requires the repo path in *Settings → Resources
→ File Sharing* (or `docker cp` the projection DB into the container).

## Deliverable

`Data Lineage Impact Platform-10/Throughline - Final.dc.html` is regenerated from
the captured evidence by `scripts/floci/generate_throughline_model.py` (idempotent,
asserts a 23/18/5/8-VERIFIED-92 round trip). It renders the real petclinic
lineage — controllers, tables, per-column confidence, real citations, repository
invocations, live impact simulation — and adds a **Review & Approve** mode: the
human gate (L09) where a reviewer verifies the 15 static-only PROBABLE-70 edges
against their evidence before approval unlocks fenced publication, mirroring the
`review_proposal` gate the E2E exercised for the baseline, incremental and
deployment paths. The prior revenue-domain demo is preserved as
`Throughline - Final.revenue-demo.bak.html`.
