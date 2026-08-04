# Lineage Collector Prototype

A locally runnable M1 walking skeleton for evidence-first lineage collection. It accepts one signed repository push, classifies and analyzes a seeded Python pipeline, stores immutable evidence, consolidates confidence, routes a proposal through human review, publishes with a fencing token, and serves version-pinned lineage and impact queries.

The application is intentionally local: FastAPI + SQLite + a write-once object directory on the backend, and React + TypeScript + Vite on the frontend. It does not require AWS credentials or an LLM key.

## Prerequisites

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer with npm

## Local setup

```bash
make setup
```

This installs the locked Python environment from `apps/api/uv.lock` and JavaScript dependencies from `package-lock.json`.

## Run locally

```bash
make dev
```

Open the UI at [http://127.0.0.1:5173](http://127.0.0.1:5173). The API health endpoint is [http://127.0.0.1:8000/healthz](http://127.0.0.1:8000/healthz), and interactive API documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

Generated state is written under `data/` and is ignored by Git. Stop both processes with `Ctrl-C`.

## Reset the deterministic demo

```bash
make reset
```

Reset removes only the generated local object directory, clears the local control tables, reloads the checked-in catalog snapshot, and restores the active `staging` pointer to `v1`. The same operation is available while the API is running through `POST /api/demo/reset`.

## Demo walkthrough

1. Open **Operations** and select **Run seeded collection**. The backend resets state, signs the seeded delivery, verifies it, and processes `payments-pipeline` to `IN_REVIEW`.
2. Open the resulting **Run timeline**. Confirm the ordered stages `QUEUED` through `IN_REVIEW`, the shared correlation ID, and SCA/runtime evidence references.
3. Open **Review queue**, select the payments proposal, and inspect the three added edges. Confidence (`HIGH`) and corroboration (`ELEMENT`) are deliberately separate; SCA citations and evidence checksums are visible.
4. Enter a rationale and choose **Approve and publish**. The manifest is written immutably, `v2` is staged and verified, and the active pointer advances with fencing token `1`.
5. Open **Lineage explorer**. Select an edge with the keyboard or pointer to inspect its mechanisms, citation, and checksum.
6. Run a `COLUMN_DROP` impact analysis. High-confidence downstream edges produce a `BLOCK` verdict. Change direction or depth to exercise bounded traversal.
7. Running the same signed delivery again returns `DUPLICATE` and creates no second run.

## Test and build

```bash
make test
make build
make verify
```

- `make test` runs the backend domain/API/walking-skeleton suite and the frontend component suite.
- `make build` performs strict TypeScript compilation and a Vite production build.
- `make verify` runs both commands as the local pre-handoff gate. Browser E2E is added as a separate command in the completion step.

## Project map

```text
apps/api/           FastAPI app, domain services, SQLite control state
apps/web/           React control-room UI
fixtures/           catalog and seeded payments-pipeline source
packages/contracts/ strict JSON Schemas at component boundaries
tests/              cross-component walking-skeleton and documentation tests
docs/               approved design, implementation plan, coverage, ambiguities
data/               generated local state (ignored)
```

The prototype coverage and deliberate deferrals are listed in [docs/prototype-coverage.md](docs/prototype-coverage.md). PRD gaps, enterprise context seams, cross-document conflicts, and reversible local decisions are recorded in [docs/prd-ambiguities.md](docs/prd-ambiguities.md).
