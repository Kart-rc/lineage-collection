# Navigation — where everything is and how to run it

A guided map of the repository. Start here if you are new, returning after a gap, or
trying to work out which of the four workspaces owns the thing you are looking at.

- **New here?** Read [What this is](#what-this-is), then [Run it in five minutes](#run-it-in-five-minutes).
- **Looking for a file?** Jump to [Where things live](#where-things-live).
- **Trying to run something specific?** See [Command reference](#command-reference).
- **Debugging?** See [Troubleshooting](#troubleshooting).

---

## What this is

A locally runnable, production-shaped implementation of **evidence-first lineage
collection**. It works out which database tables and columns a service reads and
writes, proves those findings rather than asserting them, and keeps the answer
current as code changes.

The organising idea is that no edge enters the graph without a citation. Static
analysis reads committed Git blobs and proposes an edge with a `file:line` behind it.
An optional runtime step compiles and runs a generated harness to check whether that
claim actually holds. Confidence is then a fixed function of how many independent
mechanisms agreed — not a tuned score.

Everything downstream depends on that: a reviewer approves evidence rather than
guesses, impact analysis reports severity weighted by confidence, and publication
advances behind a monotonic fence so two writers can never both win.

---

## The four workspaces

| Workspace | What it is | README |
|---|---|---|
| `apps/api` | Collection engine and product API. Python, FastAPI, SQLite | [README](../apps/api/README.md) |
| `apps/web` | Operator control room. React, TypeScript, Vite | [README](../apps/web/README.md) |
| `infra` | CDK stacks, OCI packaging, generated Step Functions | [README](../infra/README.md) |
| `packages/contracts` | JSON Schemas asserted at component boundaries | — |

Supporting directories:

| Path | Contents |
|---|---|
| `docs/` | Architecture, plans, acceptance specs, component PRDs |
| `fixtures/` | Catalog snapshot and seeded repositories used by tests and the demo |
| `scripts/` | Workflow export, acceptance runners, AWS deploy/smoke/cleanup |
| `tests/` | Cross-component, acceptance, AWS-gated and documentation tests |
| `data/` | Generated local state. Ignored by Git, safe to delete |

---

## Run it in five minutes

### Prerequisites

- Python 3.12 or 3.13, and [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer
- Docker Desktop — only for `make package-aws`
- AWS CLI v2 and an approved account — only for the explicit ephemeral-AWS flow

No AWS credentials and no LLM key are needed for local use.

### Install and start

```bash
make setup
make dev
```

Open <http://127.0.0.1:5173>. The API is on <http://127.0.0.1:8000>, with interactive
docs at `/docs` and a health probe at `/healthz`.

Stop both with `Ctrl-C`. Generated state lands in `data/` and is gitignored.

### Walk the demo

1. **Operations** → *Run seeded collection*. The backend resets state, signs a seeded
   delivery, verifies it, and processes `payments-pipeline` through to `IN_REVIEW`.
2. **Run timeline** → confirm the ordered stages, the shared correlation ID, and the
   SCA and runtime evidence references.
3. **Review queue** → open the payments proposal. Inspect the three added edges.
   Confidence and corroboration are deliberately separate signals; citations and
   evidence checksums are visible on each edge.
4. Enter a rationale and choose **Approve and publish**. The manifest is written
   immutably, `v2` is staged and verified, and the active pointer advances with
   fencing token `1`.
5. **Lineage explorer** → select an edge to inspect its mechanisms, citation and
   checksum.
6. Run a `COLUMN_DROP` impact analysis. High-confidence downstream edges produce a
   `BLOCK` verdict. Vary direction and depth to exercise bounded traversal.
7. Re-run the same signed delivery. It returns `DUPLICATE` and creates no second run.

Reset at any time with `make reset`, or `POST /api/demo/reset` while running.

---

## Collect a real repository

The demo uses a fixture. To analyse an actual Java/Spring checkout:

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

Add `--runtime-verification` to also execute the corroboration harness.

Analysis never runs Maven, Gradle, tests, application code, hooks or repository
executables. It reads canonical committed blobs, verifies the origin, revision and
tree identity, and stops at `IN_REVIEW`.

Spring Petclinic is the worked acceptance example — see the
[root README](../README.md) for the pinned revision and its expected 23-edge oracle.

---

## Where things live

### Backend, by layer

```
apps/api/src/lineage_api/
├── domain/        pure rules: confidence bands, URNs, proposal states, impact severity
├── application/   workflow definitions, stage handlers, ports, collection orchestration
├── services/      analyzers, resolver, consolidation, review, publisher, orchestration
├── infrastructure/ adapters: SQLite, local/remote Git, and aws/
├── entrypoints/   AWS Lambda handlers and the SCA worker
├── runtime/       SDK, producer, transport, OTel and OpenLineage adapters
├── main.py        FastAPI routes
├── cli.py         collect-checkout, worker, reset
└── config.py      environment resolution, fails closed on secrets
```

Dependencies point inward. `domain` knows nothing above it; `infrastructure` is
reached through `application/ports.py`.

### Finding your way to a concept

| If you want… | Look at |
|---|---|
| How confidence is computed | `domain/confidence.py`, then `application/consolidation.py` |
| What the workflow stages are | `application/workflows/definitions.py` — the single authority |
| How Java code is analysed | `services/java_spring_sca.py` |
| How the runtime harness works | `services/java_runtime_verification.py`, `application/java_runtime_stage.py` |
| How names resolve to URNs | `services/resolver.py` and `fixtures/catalog/` |
| The proposal state machine | `domain/proposals.py`, then `services/review.py` |
| Publishing and fencing | `services/publisher.py` |
| Queue, leases and redrive | `infrastructure/local_broker.py`, `infrastructure/sqlite_control.py` |
| Impact traversal | `services/query.py` |

---

## The five workflows

| Workflow | Stages | Triggered by |
|---|---|---|
| Baseline | B1–B10 | Onboarding, missing trusted base, explicit rebaseline |
| Incremental | I1–I10 | Observed-branch push, ruleset or resolver change, accepted correction |
| PR Gate | P1–P8 | PR opened, synchronised or reopened; target environment change |
| Deployment | D1–D6 | A canonical deployment outcome carrying an exact artifact digest |
| Nightly | N1–N6 | Reconciliation schedule, drift sampling, manifest audit |

Selection is driven entirely by the event's `eventType` in `services/intake.py`.

> **Current behaviour worth knowing.** Every product path — CLI, `POST /api/collections`
> and the demo — emits `repo.push`, so all of them run **Incremental**. Baseline is
> implemented and tested but has no live trigger. Separately, Incremental requires
> `changedFiles` to equal the analyzer's full expected scope, so it is a full
> re-analysis rather than a partial one.

---

## Command reference

Every command runs from the repository root.

### Everyday

| Command | Does |
|---|---|
| `make setup` | Install locked Python and JavaScript dependencies |
| `make dev` | Run API and web UI together |
| `make reset` | Restore the seeded demo state |
| `make test` | Backend, web and infra suites |
| `make build` | Strict TypeScript build and Vite production bundle |
| `make verify` | `test` then `build` — the local pre-handoff gate |

### Targeted

```bash
uv run --project apps/api --extra dev pytest -q          # backend only
npm test --workspace apps/web -- --run                   # web only
npm test --workspace infra -- --run                      # infra only
uv run --project apps/api python -m lineage_api.cli worker --drain --max-messages 100
```

### Generated artefacts

| Command | Does |
|---|---|
| `make workflow-export` | Regenerate ASL from the Python workflow definitions |
| `make workflow-check` | Fail if checked-in ASL has drifted |
| `make architecture` | Render the architecture diagram |
| `make architecture-check` | Fail if the rendering is stale |
| `make synth` | Offline CDK assembly, no credentials |
| `make package-aws` | Build wheel and OCI archives — needs Docker |

Never hand-edit `infra/workflows/*.asl.json` or the generated architecture HTML.

### Acceptance and AWS

| Command | Does |
|---|---|
| `make acceptance-smoke` | Deterministic local acceptance gate with evidence manifests |
| `make aws-deploy` / `aws-smoke` / `aws-cleanup` | Ephemeral AWS, each behind its own explicit opt-in |

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LINEAGE_WEBHOOK_SECRET` | **required** | HMAC key for every signed delivery |
| `LINEAGE_DEV_MODE` | `0` | Permits the published demo secret. Local development only |
| `LINEAGE_API_TOKEN` | unset | When set, every route but `/healthz` requires a bearer token |
| `LINEAGE_DATA_DIR` | `./data` | SQLite database and write-once object directory |
| `LINEAGE_ALLOW_LOCAL_REPOSITORY_SOURCES` | `false` | Permit `LOCAL_CHECKOUT` sources |
| `LINEAGE_JAVA_HOME` | unset | JDK for the runtime harness |
| `LINEAGE_API_PORT` | `8000` | API port. The web proxy follows it automatically |
| `LINEAGE_WEB_PORT` | `5173` | Web dev-server port |
| `LINEAGE_ESTATE_DIR` | `/private/tmp/lineage-estate` | Where the optional external corpora live for corpus-backed tests |

**Ports already in use?** `LINEAGE_API_PORT=8021 LINEAGE_WEB_PORT=5181 make dev` moves
the whole stack, proxy included, so it can run alongside another instance.

**On secrets.** `LINEAGE_WEBHOOK_SECRET` authenticates every signed input — pushes,
deployment outcomes and runtime observations alike. Resolution fails closed: a
missing secret, the published demo secret, or anything under 16 bytes is refused
outside development mode. `make dev` and `make reset` set `LINEAGE_DEV_MODE=1` for
you, which is why the local walkthrough needs no configuration.

**On the API token.** Unset leaves the API open, which is right for a single-operator
local run. Set it for anything reachable by more than one person; it gates reads too,
because the graph discloses schema and topology. The web UI does not yet send a
token, so leave it unset when using the UI.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `LINEAGE_WEBHOOK_SECRET must be set` | Running the API outside `make dev`. Export a secret of 16+ bytes, or set `LINEAGE_DEV_MODE=1` for local work |
| `401 UNAUTHORIZED` on every route | `LINEAGE_API_TOKEN` is set. Send `Authorization: Bearer <token>`, or unset it for the UI |
| `Address already in use` on `make dev` | Something holds 8000 or 5173. Set `LINEAGE_API_PORT` and `LINEAGE_WEB_PORT` |
| Corpus-backed tests skip | Expected. They need optional external checkouts under `LINEAGE_ESTATE_DIR`; the skip message names the exact path and repository |
| `INTEGRATION_REQUIRED` on a real repo | The analyzer found something it cannot safely classify. Usual causes: no trusted `db/<profile>/schema.sql`, several candidates, an H2 profile, or non-literal build cells |
| `POINTER_NOT_FOUND` | The target environment has no pointer. Only `staging` is seeded — run `make reset` first |
| `UNKNOWN_ANALYZER_PACK` / `PROFILE_MISMATCH` | The pack, ruleset, framework and profile combination is outside the closed registry in `services/analyzer_registry.py` |
| `DUPLICATE` on re-submission | Correct behaviour. Identical work returns the stored result and creates no second run |
| `make workflow-check` fails | The ASL drifted. Run `make workflow-export` and commit the result |
| Web tests fail after an API change | `apps/web/src/api/types.ts` mirrors backend responses. Update it alongside |

---

## Further reading

| Document | Contents |
|---|---|
| [Root README](../README.md) | Full feature narrative, Petclinic oracle, AWS mapping |
| [Target architecture](architecture/lineage-platform-target.md) | The canonical production topology |
| [Acceptance specification](acceptance/lineage-platform-acceptance.md) | What must hold for a release |
| [Implementation coverage](prototype-coverage.md) | What is built and what remains gated |
| [Resolved ambiguities](prd-ambiguities.md) | PRD gaps, enterprise seams, reversible decisions |
| [Component PRDs](component-prds/) | The 18 source PRDs the prototype was designed from |
