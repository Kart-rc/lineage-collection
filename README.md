# Lineage Collector

A locally runnable, production-shaped implementation of evidence-first lineage collection. It accepts signed repository events, runs deterministic Baseline and Incremental collection, validates optional runtime evidence, creates and reviews proposals, publishes with fencing, handles exact-artifact deployment promotion, evaluates a bounded read-only PR gate, and serves version-pinned lineage and impact queries.

The default operator path remains local: FastAPI + SQLite + a write-once object directory on the backend, and React + TypeScript + Vite on the frontend. The same repository also contains nine independently addressable Lambda handlers, an SCA Fargate worker, four generated Step Functions workflows, concrete AWS adapters, CDK stacks, deterministic OCI packaging, and guarded ephemeral-AWS verification. Local use does not require AWS credentials or an LLM key.

## Prerequisites

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer with npm
- Docker Desktop only for `make package-aws`
- AWS CLI v2 and an approved AWS account only for the explicit ephemeral-AWS flow

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

To drain durable local work without the web process, run:

```bash
uv run --project apps/api python -m lineage_api.cli worker --drain --max-messages 100
```

Use `--once` instead of `--drain` to process at most one available command.

## Collect an exact Java/Spring checkout

`collect-checkout` analyzes canonical committed Git blobs without running Maven, Gradle, tests,
application code, hooks, or repository executables. It verifies the credential-free origin, exact
revision, index/tree identity, bounded tracked scope and immutable scope digest before creating a
signed delivery. That delivery then follows the normal SQLite outbox, queue, lease, I1–I10 stage
ledger, evidence, consolidation and review path. Successful collection stops at `IN_REVIEW`; it
does not approve or publish.

```bash
export LINEAGE_DATA_DIR=/tmp/lineage-real-state
export LINEAGE_WEBHOOK_SECRET=change-me-local-only

uv run --project apps/api python -m lineage_api.cli collect-checkout \
  --checkout /absolute/path/to/spring-service \
  --origin https://github.com/acme/spring-service \
  --revision <exact-40-or-64-character-lowercase-commit> \
  --repository spring-service \
  --environment staging \
  --platform postgres \
  --system orders \
  --analyzer-pack java-spring-data-jpa-v1 \
  --ruleset spring-data-rules-v1 \
  --profile postgres
```

The v1 pack is repository-neutral. It reconciles literal root Maven and Gradle Spring Boot/Data JPA
cells, analyzes production Java under `src/main/java`, and selects exactly one
`db/<profile>/schema.sql`. Conflicting or dynamic build cells and missing or multiple profile
schemas return `INTEGRATION_REQUIRED`. Seed data, user/setup scripts and test Java are outside this
static production scope. The H2 profile is deliberately approximation-only and returns
`INTEGRATION_REQUIRED`; use a trusted PostgreSQL or MySQL schema profile to authorize tables.

The bounded JSON result contains only identifiers, digests, status, stage names and counts. Static
`exact=true` means the source citation is exact, not that the operation ran. Until a validated
runtime session is joined, `runtimeStatus` remains `NOT_PROVIDED`.

Spring Petclinic is an acceptance example, not a special case in production code:

```bash
git clone https://github.com/spring-projects/spring-petclinic.git /tmp/spring-petclinic
git -C /tmp/spring-petclinic checkout --detach 88e37c15cf6fc8490b01bc3e8e2c800cec1ac272

uv run --project apps/api python -m lineage_api.cli collect-checkout \
  --checkout /tmp/spring-petclinic \
  --origin https://github.com/spring-projects/spring-petclinic \
  --revision 88e37c15cf6fc8490b01bc3e8e2c800cec1ac272 \
  --repository spring-petclinic \
  --environment staging \
  --platform postgres \
  --system petclinic \
  --analyzer-pack java-spring-data-jpa-v1 \
  --ruleset spring-data-rules-v1 \
  --profile postgres
```

At that pinned revision the accepted oracle is 15 static edges (10 reads and 5 writes), zero
unresolved invocations, proposal `IN_REVIEW`, and runtime `NOT_PROVIDED`. Repeating the identical
command returns `DUPLICATE` with the same command, run and proposal and no extra ledger effects.

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
make synth
make package-aws
```

- `make test` runs the backend domain/API/walking-skeleton suite and the frontend component suite.
- `make build` performs strict TypeScript compilation and a Vite production build.
- `make verify` runs both commands as the local pre-handoff gate. The documented walkthrough was also exercised in a real Chromium session for the prototype handoff.
- `make synth` creates the no-credential fixture CDK assembly under `infra/cdk.out/`.
- `make package-aws` builds the Python wheel plus `linux/amd64` Lambda and `linux/arm64` SCA OCI archives. Verify exact image/archive digests and handler inventory in `infra/dist/runtime-build-metadata.json` and `infra/dist/package-manifest.json`.

## Workflow trigger table

Trigger choice is versioned policy. Receipt is acknowledged only after the canonical event and its durable command or no-impact decision exist.

| Flow | Trigger it | Do not trigger it |
|---|---|---|
| Baseline B1–B10 | Repository/system onboarding, missing trusted base, explicit full rebaseline, or a major unsupported determinant/schema change | Every push, PR update, merge, or deployment |
| Incremental I1–I10 | Observed-branch push, affected ruleset/resolver/policy change, accepted correction, late validated runtime evidence, or missing-package remediation | Unchanged paths/determinants with a complete no-impact proof |
| PRGate P1–P8 | PR open/synchronize/reopen, target-environment change, manual rerun, or active-pointer refresh | Merge, deployment, Nightly, LLM completion, or runtime-session close |
| Deployment D1–D6 | Canonical succeeded, failed, or rollback deployment outcome with exact artifact digest and authoritative provider ordering | Source merge alone |
| Nightly N1–N6 | Reconciliation schedule, drift/rebuild sample, bounded stale derivation, or manifest/cache audit | User-facing interactive requests |

PRGate is read-only except for its check/audit record. It rechecks both the PR head and environment pointer; incomplete, stale, degraded, truncated, or timed-out analysis returns `WARN`, never a false `PASS`.

## Local-to-AWS mapping

One Python package is reused, but deployment boundaries are explicit and independently scalable.

| Concern | Local adapter | AWS adapter/deployment |
|---|---|---|
| Receipt, command, lease, stage ledger | SQLite transactions and simulated clock | DynamoDB conditional writes and lease epochs |
| Queue lanes | SQLite broker with FIFO groups and deterministic priority | Interactive/events FIFO SQS plus bounded batch SQS, DLQs, partial-batch redrive and reserved headroom |
| Workflow | Python application definitions | Four versioned Standard Step Functions aliases; Deployment remains one D1–D6 Lambda |
| Static analysis | Local Python SCA module | Callback-token ARM64 Fargate task with heartbeat |
| Immutable evidence/packages | Write-once checksummed files | Versioned, encrypted S3; production adds Object Lock and cross-region replication |
| Runtime observations | Closed-schema local session store | Kinesis partitioning plus the same OpenLineage/custom SDK/OTel validation contracts |
| Projection and pointer | Versioned SQLite graph and conditional pointer | Neptune idempotent merge plus DynamoDB fenced pointer |
| Product/API | Local FastAPI and React | Versioned Lambda image targets, API Gateway and canary aliases |

The local implementation proves behavior and recovery. CDK assertions and offline synth prove topology. A real AWS deployment, canary, scale window and recovery drill remain separate evidence gates.

## Generated workflow safety

`apps/api/src/lineage_api/application/workflows/definitions.py` is the workflow authority. The checked-in files under `infra/workflows/` are deterministic exports; do not edit ASL or the generated contract by hand.

```bash
make workflow-export
make workflow-check
```

`make workflow-check` fails if B1–B10, I1–I10, P1–P8, N1–N6, D1–D6, timeouts, attempts, terminals, or generated ASL drift from the Python definitions. `data/`, `apps/web/dist/`, `infra/dist/`, and `infra/cdk.out/` are generated outputs and may be recreated. Do not treat them as authoritative source or commit them.

## Fault injection and acceptance evidence

Run the deterministic local acceptance gate:

```bash
make acceptance-smoke
```

It writes content-addressed, schema-valid `AcceptanceEvidenceManifest` records under a fresh gitignored `data/acceptance/<run>/` directory. The current local suite exercises named crash/redrive, checksum equivalence, lane headroom/fairness, and evidence integrity. AWS-only 100/s, 10k burst, 10k-repository/12-hour, availability and DR rows are emitted as `AWS_REQUIRED`, not `PASS`.

Run only the named crash/redrive scenario with:

```bash
uv run --project apps/api --extra dev pytest -q tests/acceptance/test_replay_and_faults.py
```

The acceptance runner returns nonzero when a scenario or recorded manifest is `FAIL`.

## AWS ephemeral verification

No AWS command runs implicitly. `scripts/deploy_ephemeral_aws.sh` requires explicit account, primary/recovery regions, three availability zones, a `lineage-e2e-*` namespace, approved PrivateLink and paging values, a clean commit, and `ALLOW_LINEAGE_EPHEMERAL_AWS=1`. It packages exact OCI digests, creates repositories first, pushes those digests, then deploys the remaining stacks.

Set all values from approved enterprise/account context; the angle-bracket values below are labels,
not defaults:

```bash
export AWS_PROFILE=<approved-profile>
export AWS_ACCOUNT_ID=<approved-12-digit-account>
export AWS_REGION=<approved-primary-region>
export LINEAGE_SECONDARY_REGION=<approved-recovery-region>
export LINEAGE_PRIMARY_AVAILABILITY_ZONES=<az-a,az-b,az-c>
export LINEAGE_EPHEMERAL_PREFIX=lineage-e2e-<unique-suffix>
export LINEAGE_ENTERPRISE_ENDPOINT=https://<approved-private-dns-name>
export LINEAGE_ENTERPRISE_ENDPOINT_SERVICE_NAME=<approved-vpce-service-name>
export LINEAGE_PAGING_TOPIC_ARN=<approved-sns-topic-arn>
export ALLOW_LINEAGE_EPHEMERAL_AWS=1
```

```bash
make aws-deploy
make aws-smoke
make aws-cleanup
```

- `make aws-deploy` requires the deploy opt-in and environment values documented by the script.
- `make aws-smoke` requires `ALLOW_LINEAGE_EPHEMERAL_AWS_SMOKE=1` and the generated CDK outputs file. It seeds a checksummed Incremental input and verifies I1–I10, DynamoDB/S3 evidence, CloudWatch correlation and duplicate no-effect.
- `make aws-cleanup` requires the separate destructive acknowledgement `ALLOW_LINEAGE_EPHEMERAL_AWS_CLEANUP=DESTROY` and deletes only the validated namespace in the validated account/regions.

Production mode remains deletion-protected and Object-Locked. Only the strict ephemeral mode is disposable. Without approved credentials and opt-ins, the correct outcome is `AWS_REQUIRED`.

## Architecture and delivery sources

- [Normative architecture and six Mermaid views](docs/plans/2026-08-05-lineage-collection-architecture-refactor-design.md)
- [Executable implementation plan and Tasks 1–22](docs/plans/2026-08-05-lineage-collection-architecture-refactor.md)
- [Acceptance specification](docs/acceptance/lineage-platform-acceptance.md)
- [Build-ready B01–B16 PRDs](docs/build-prds/README.md)
- [Current implementation/evidence coverage](docs/prototype-coverage.md)
- [Remaining enterprise context and resolved ambiguities](docs/prd-ambiguities.md)

## Project map

```text
apps/api/           domain/application core, FastAPI, local and AWS adapters/entry points
apps/web/           React control-room UI
infra/              CDK stacks, runtime packaging and generated ASL
fixtures/           catalog and seeded payments-pipeline source
packages/contracts/ strict JSON Schemas at component boundaries
scripts/            workflow export, acceptance, AWS deploy/smoke/cleanup
tests/              cross-component, acceptance, AWS-gated and documentation tests
docs/               approved design, implementation plan, coverage, ambiguities
data/               generated local state (ignored)
```

The implementation coverage and deliberate external gates are listed in [docs/prototype-coverage.md](docs/prototype-coverage.md). PRD gaps, enterprise context seams, resolved conflicts, and reversible local decisions are recorded in [docs/prd-ambiguities.md](docs/prd-ambiguities.md).

The 18 source component PRDs used to design the prototype are included verbatim in [docs/component-prds](docs/component-prds).
