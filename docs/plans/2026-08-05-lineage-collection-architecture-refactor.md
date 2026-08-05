# Lineage Collection Architecture Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor the local lineage collector to prove durable, idempotent, resumable Baseline, Incremental, PRGate, Deployment, and runtime-lineage behavior while defining build-ready AWS infrastructure and executable acceptance contracts.

**Architecture:** Keep the FastAPI/worker codebase locally deployable as one unit, but move workflow behavior behind explicit application ports. Persist receipts, commands, lease epochs, immutable stage outputs, coverage manifests, transactional outbox entries, lineage packages, and fenced publication state in SQLite/files; production adapters target DynamoDB, S3, SQS, Step Functions, Kinesis, and Neptune without changing domain behavior.

**Tech Stack:** Python 3.12, FastAPI, SQLite, JSON Schema, pytest, React/TypeScript/Vite, AWS CDK TypeScript, Step Functions ASL, SQS, DynamoDB, S3, Kinesis, Neptune, OpenTelemetry, OpenLineage.

---

## Scope and execution rules

- Work in `/Users/sowmiyamohankumar/Documents/lineage-collector-v1/.worktrees/lineage-prototype` on `codex/lineage-prototype`.
- Use test-driven development for behavior: write a failing focused test, verify the failure, add the minimum implementation, rerun the focused test, then run the relevant suite.
- After every task: inspect `git diff`, run `git diff --check`, perform a local self-review, update `/tmp/refactor-lineagecollector.md`, and commit only the task's files.
- Do not use CodeRabbit.
- Do not contact real GitHub, Jenkins, catalog, Bedrock, OTel, or AWS endpoints. Use contract fixtures and ephemeral/local adapters until enterprise CTX values and credentials are supplied.
- The feasibility exit is a local correctness proof plus synthesizable CDK and adapter contract boundaries. Production scale, multi-AZ, and DR claims require the prescribed AWS load/drill evidence; they are not claimed from local tests.

## Phase 1 — executable specification and durable core

### Task 1: Create the normative acceptance specification

**Files:**
- Create: `docs/acceptance/lineage-platform-acceptance.md`
- Create: `packages/contracts/acceptance-evidence-manifest.schema.json`
- Modify: `tests/test_documentation.py`
- Modify: `docs/component-prds/99-implementation-readiness-review.md`

**Step 1: Write the failing documentation test**

Add assertions that the acceptance document exists, defines `B01` through `B16`, includes the test
levels and release cadences, maps the historical component-PRD `Test Suite §` labels, and is linked
as the normative executable index from the readiness review.

```python
def test_acceptance_spec_covers_every_build_unit_and_cadence() -> None:
    acceptance = _read("docs/acceptance/lineage-platform-acceptance.md")
    for number in range(1, 17):
        assert f"B{number:02d}" in acceptance
    for cadence in ("Every PR", "Every deployment", "Nightly", "Weekly", "Monthly", "Quarterly"):
        assert cadence in acceptance
    assert "AcceptanceEvidenceManifest" in acceptance
```

**Step 2: Run the test and verify the missing document failure**

Run: `uv run --project apps/api --extra dev pytest -q tests/test_documentation.py`
Expected: FAIL with `Missing required documentation: docs/acceptance/lineage-platform-acceptance.md`.

**Step 3: Write the specification and evidence schema**

Define `Bxx-AC-nnn` rows with requirement links, Given/When/Then, level, fixture, oracle, environment,
threshold, fault point, evidence, cadence, owner, and release consequence. Define the strict manifest:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:lineage:contract:acceptance-evidence-manifest:1.0.0",
  "type": "object",
  "additionalProperties": false,
  "required": ["schemaVersion", "buildId", "acceptanceId", "artifactDigest", "outcome", "evidenceRefs"],
  "properties": {
    "schemaVersion": {"const": "1.0.0"},
    "buildId": {"pattern": "^B(0[1-9]|1[0-6])$"},
    "acceptanceId": {"pattern": "^B(0[1-9]|1[0-6])-AC-[0-9]{3}$"},
    "artifactDigest": {"type": "string", "minLength": 1},
    "outcome": {"enum": ["PASS", "FAIL", "WAIVED"]},
    "evidenceRefs": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
  }
}
```

Update the readiness review so the new file replaces unresolved `Test Suite §` references as the
normative index while retaining historical source labels.

**Step 4: Run focused and contract tests**

Run: `uv run --project apps/api --extra dev pytest -q tests/test_documentation.py apps/api/tests/test_contracts.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add docs/acceptance packages/contracts/acceptance-evidence-manifest.schema.json tests/test_documentation.py docs/component-prds/99-implementation-readiness-review.md
git commit -m "docs: define executable lineage acceptance contract"
```

### Task 2: Add versioned durable-control contracts

**Files:**
- Create: `packages/contracts/durable-command.schema.json`
- Create: `packages/contracts/stage-execution.schema.json`
- Create: `packages/contracts/coverage-manifest.schema.json`
- Create: `packages/contracts/outbox-event.schema.json`
- Test: `apps/api/tests/test_contracts.py`
- Modify: `apps/api/src/lineage_api/contracts.py`

**Step 1: Add failing strict-schema fixtures**

Test valid canonical fixtures and rejection of unknown fields, empty identity, missing determinant,
invalid lease epoch, and a `COMPLETE` coverage manifest with unaccounted expected scope.

```python
def test_stage_execution_rejects_completion_without_output_ref(contract_registry) -> None:
    payload = stage_execution_fixture(status="COMPLETED")
    payload.pop("outputRef")
    assert "outputRef" in {error.path for error in contract_registry.validate("stage-execution", payload)}
```

**Step 2: Verify the tests fail because contracts are unknown**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/test_contracts.py`
Expected: FAIL with `Unknown contract`.

**Step 3: Add the JSON Schemas and semantic validation hook**

Add cross-field semantic validation in `ContractRegistry.validate` for completeness constraints that
JSON Schema cannot express cleanly: `expected == completed + reused + skipped + unsupported + quarantined + failed` and `COMPLETE` forbids failed/unsupported/unaccounted items.

**Step 4: Run the contract suite**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/test_contracts.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add packages/contracts apps/api/src/lineage_api/contracts.py apps/api/tests/test_contracts.py
git commit -m "feat: add durable workflow contracts"
```

### Task 3: Introduce explicit application ports and immutable models

**Files:**
- Create: `apps/api/src/lineage_api/application/__init__.py`
- Create: `apps/api/src/lineage_api/application/models.py`
- Create: `apps/api/src/lineage_api/application/ports.py`
- Test: `apps/api/tests/application/test_models.py`

**Step 1: Write failing model tests**

Cover deterministic idempotency keys, UTC deadline parsing, immutable dataclasses, lease epochs, and
coverage-set accounting.

```python
def test_stage_key_changes_only_when_a_determinant_changes() -> None:
    base = StageIdentity("INCREMENTAL", "payments", "sha256:a", "ANALYZE", "det:v1", "1.0.0")
    assert base.idempotency_key() == replace(base).idempotency_key()
    assert base.idempotency_key() != replace(base, determinant_digest="det:v2").idempotency_key()
```

**Step 2: Run and verify import failure**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_models.py`
Expected: FAIL because `lineage_api.application.models` does not exist.

**Step 3: Implement models and Protocol ports**

Create frozen models for `Command`, `Lease`, `StageResult`, `CoverageManifest`, `OutboxEvent`, and
`LineagePackage`. Define `Protocol` interfaces for receipts, command store, leases, artifacts,
outbox, lane broker, catalog snapshots, proposals, publication, projections, clock, and telemetry.
Ports contain no SQLite, FastAPI, boto3, or AWS types.

**Step 4: Run model tests and backend suite**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_models.py apps/api/tests/test_contracts.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/application apps/api/tests/application
git commit -m "refactor: define lineage application ports"
```

### Task 4: Add additive SQLite migrations for commands, leases, stages, coverage, and outbox

**Files:**
- Create: `apps/api/src/lineage_api/migrations.py`
- Modify: `apps/api/src/lineage_api/db.py`
- Test: `apps/api/tests/test_db.py`

**Step 1: Write failing migration tests**

Verify a fresh database reaches schema version 2 and an existing version-1 fixture upgrades without
losing events, runs, proposals, graph versions, or pointers. Assert tables and uniqueness constraints
for `commands`, `command_attempts`, `stage_results`, `coverage_manifests`, and `outbox_events`.

**Step 2: Run and verify missing-table failure**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/test_db.py`
Expected: FAIL on missing durable-control tables.

**Step 3: Implement ordered additive migrations**

Move schema creation into numbered migrations. Never drop or rewrite user data. Add indexes for due
commands, expired leases, undelivered outbox events, workflow/scope, and coverage state.

**Step 4: Run DB and walking-skeleton tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/test_db.py tests/test_walking_skeleton.py`
Expected: PASS and the existing walking skeleton remains unchanged.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/db.py apps/api/src/lineage_api/migrations.py apps/api/tests/test_db.py
git commit -m "feat: add durable control-state migrations"
```

### Task 5: Implement SQLite command leasing and stage idempotency

**Files:**
- Create: `apps/api/src/lineage_api/infrastructure/__init__.py`
- Create: `apps/api/src/lineage_api/infrastructure/sqlite_control.py`
- Test: `apps/api/tests/infrastructure/test_sqlite_control.py`

**Step 1: Write failing lease and replay tests**

Test unique submission by idempotency key, atomic claim, lease renewal, expired-lease stealing with a
higher epoch, rejection of completion by the stale epoch, deterministic completed-stage reuse, and
bounded attempt exhaustion.

```python
def test_stale_worker_cannot_complete_after_lease_is_stolen(store, clock) -> None:
    command = store.submit(command_fixture())
    first = store.claim(command.command_id, "worker-a", lease_seconds=30)
    clock.advance(seconds=31)
    second = store.claim(command.command_id, "worker-b", lease_seconds=30)
    assert second.epoch > first.epoch
    with pytest.raises(StaleLeaseError):
        store.complete(command.command_id, first, output_ref="object://old")
```

**Step 2: Verify failure**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/infrastructure/test_sqlite_control.py`
Expected: FAIL because the adapter is absent.

**Step 3: Implement transactional claim/renew/complete/fail operations**

Use `BEGIN IMMEDIATE`, conditional `UPDATE ... WHERE lease_epoch = ?`, and immutable `stage_results`.
A completed stage with the same key returns the existing reference; a different checksum is
`IDEMPOTENCY_CONFLICT`.

**Step 4: Run focused tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/infrastructure/test_sqlite_control.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/infrastructure apps/api/tests/infrastructure
git commit -m "feat: add leased durable command store"
```

### Task 6: Implement the transactional outbox and local lane broker

**Files:**
- Create: `apps/api/src/lineage_api/infrastructure/local_broker.py`
- Create: `apps/api/src/lineage_api/application/outbox.py`
- Test: `apps/api/tests/application/test_outbox.py`
- Test: `apps/api/tests/infrastructure/test_local_broker.py`

**Step 1: Write failing tests**

Cover state plus outbox atomicity, delivery crash before acknowledgment, duplicate delivery, FIFO
ordering within a group, parallel groups, PR-head supersession, bulk fairness, bounded retries, and
DLQ movement.

**Step 2: Run and verify failures**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_outbox.py apps/api/tests/infrastructure/test_local_broker.py`
Expected: FAIL because the dispatcher and broker do not exist.

**Step 3: Implement minimal dispatcher and simulated broker**

The broker persists messages and exposes `publish`, `claim`, `ack`, `retry`, and `redrive`. It models
lane, group/fairness key, visible-at, attempts, deadline, and supersession; it does not emulate every
SQS API.

**Step 4: Run focused and DB tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_outbox.py apps/api/tests/infrastructure/test_local_broker.py apps/api/tests/test_db.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/application/outbox.py apps/api/src/lineage_api/infrastructure/local_broker.py apps/api/tests/application/test_outbox.py apps/api/tests/infrastructure/test_local_broker.py
git commit -m "feat: add transactional outbox and lane broker"
```

## Phase 2 — complete orchestration flows

### Task 7: Make intake acknowledgement create a durable command atomically

**Files:**
- Modify: `apps/api/src/lineage_api/services/intake.py`
- Modify: `apps/api/src/lineage_api/services/orchestration.py`
- Modify: `apps/api/src/lineage_api/dependencies.py`
- Modify: `apps/api/src/lineage_api/cli.py`
- Test: `apps/api/tests/services/test_intake.py`
- Test: `apps/api/tests/services/test_orchestration.py`
- Test: `apps/api/tests/test_cli.py`

**Step 1: Add failing durability tests**

Assert accepted intake contains exactly one durable command and outbox event before the API returns;
duplicate delivery returns the original command; injected failure between receipt and command rolls
back both; missing immutable identity quarantines with no command.

**Step 2: Verify red tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/services/test_intake.py apps/api/tests/services/test_orchestration.py`
Expected: FAIL on missing command/outbox state.

**Step 3: Refactor intake through the ports**

Keep the existing synchronous API compatibility temporarily, but have it submit and drain the local
command. Add a bounded CLI `worker --once`/`worker --drain` entry point using the same handler.
Response status `202` means durably accepted, not necessarily fully analyzed.

**Step 4: Run focused and API tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/services/test_intake.py apps/api/tests/services/test_orchestration.py apps/api/tests/test_cli.py apps/api/tests/test_api.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/intake.py apps/api/src/lineage_api/services/orchestration.py apps/api/src/lineage_api/dependencies.py apps/api/src/lineage_api/cli.py apps/api/tests/services/test_intake.py apps/api/tests/services/test_orchestration.py apps/api/tests/test_cli.py apps/api/tests/test_api.py
git commit -m "refactor: durably accept lineage commands"
```

### Task 8: Add workflow definitions and coverage-manifest verification

**Files:**
- Create: `apps/api/src/lineage_api/application/workflows/__init__.py`
- Create: `apps/api/src/lineage_api/application/workflows/definitions.py`
- Create: `apps/api/src/lineage_api/application/coverage.py`
- Test: `apps/api/tests/application/test_workflow_definitions.py`
- Test: `apps/api/tests/application/test_coverage.py`

**Step 1: Write failing topology and completeness tests**

Assert Baseline B1-B10, Incremental I1-I10, PRGate P1-P8, Deployment D1-D6, and Nightly stages are
versioned, reachable, bounded, and have success/error terminal routes. Assert incomplete accounting
cannot be marked complete.

**Step 2: Verify failures**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_workflow_definitions.py apps/api/tests/application/test_coverage.py`
Expected: FAIL on absent definitions.

**Step 3: Implement declarative definitions and verifier**

Definitions name inputs, outputs, timeout, retry class, side-effect mode, idempotency determinant,
and terminal states. Keep orchestration execution separate from the definition data.

**Step 4: Run focused tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_workflow_definitions.py apps/api/tests/application/test_coverage.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/application/workflows apps/api/src/lineage_api/application/coverage.py apps/api/tests/application
git commit -m "feat: define complete lineage workflows"
```

### Task 9: Convert Incremental to resumable stage execution

**Files:**
- Create: `apps/api/src/lineage_api/application/workflows/incremental.py`
- Modify: `apps/api/src/lineage_api/services/orchestration.py`
- Modify: `apps/api/tests/services/test_orchestration.py`
- Create: `apps/api/tests/application/test_incremental_workflow.py`

**Step 1: Add failing stage-resume tests**

Inject a crash after each existing boundary: classification write, SCA object, runtime fixture,
consolidation, proposal creation, and completion record. Redrive must reuse completed outputs, create
one proposal version, and produce byte-identical coverage and evidence manifests.

**Step 2: Verify at least one injected crash duplicates or fails today**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_incremental_workflow.py`
Expected: FAIL before resumable execution exists.

**Step 3: Implement the Incremental command handler**

Move I1-I10 decisions into the handler. Existing services remain domain collaborators. Replace the
unconditional runtime fixture with an optional validated input seam and record `NO_LINEAGE_IMPACT`,
reused determinants, tombstones, or incomplete coverage explicitly.

**Step 4: Run focused, service, and walking-skeleton tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_incremental_workflow.py apps/api/tests/services/test_orchestration.py tests/test_walking_skeleton.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/application/workflows/incremental.py apps/api/src/lineage_api/services/orchestration.py apps/api/tests/application/test_incremental_workflow.py apps/api/tests/services/test_orchestration.py
git commit -m "refactor: make incremental workflow resumable"
```

### Task 10: Implement Baseline and the Incremental differential oracle

**Files:**
- Create: `apps/api/src/lineage_api/application/workflows/baseline.py`
- Create: `apps/api/tests/application/test_baseline_workflow.py`
- Create: `tests/test_baseline_incremental_equivalence.py`
- Modify: `fixtures/repositories/payments-pipeline/expected-lineage.json`

**Step 1: Write failing Baseline and differential tests**

Test B1-B10, classification `UNKNOWN`, bounded fan-out, unsupported pack accounting, deterministic
replay, and terminal states. For seeded edits, compare normalized affected-scope manifests from a
clean Baseline at the new digest with Incremental from the old digest.

**Step 2: Run and verify failures**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_baseline_workflow.py tests/test_baseline_incremental_equivalence.py`
Expected: FAIL because Baseline is not implemented.

**Step 3: Implement Baseline using the same stages and collaborators**

Do not duplicate extraction/merge/publish logic. Baseline changes only planning, full-scope coverage,
and fan-out. Runtime/LLM remain optional and cannot conceal incomplete deterministic coverage.

**Step 4: Run focused tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_baseline_workflow.py tests/test_baseline_incremental_equivalence.py`
Expected: PASS with zero normalized differential.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/application/workflows/baseline.py apps/api/tests/application/test_baseline_workflow.py tests/test_baseline_incremental_equivalence.py fixtures/repositories/payments-pipeline/expected-lineage.json
git commit -m "feat: add deterministic baseline workflow"
```

### Task 11: Implement read-only PRGate with freshness recheck and hard deadline

**Files:**
- Create: `packages/contracts/pr-gate-result.schema.json`
- Create: `apps/api/src/lineage_api/application/workflows/pr_gate.py`
- Modify: `apps/api/src/lineage_api/migrations.py`
- Modify: `apps/api/src/lineage_api/api_models.py`
- Modify: `apps/api/src/lineage_api/main.py`
- Create: `apps/api/tests/application/test_pr_gate_workflow.py`
- Modify: `apps/api/tests/test_api.py`

**Step 1: Write failing verdict tests**

Cover exact head/environment pins, all six change types, `PASS` completeness, calibrated `BLOCK`,
LLM-only `WARN`, projection timeout `WARN`, truncation, missing/out-of-sync environment, force-push
supersession, and 120-second hard deadline. Snapshot authoritative tables before/after to prove the
flow writes no evidence, proposal, graph, pointer, or deployment state.

**Step 2: Verify failures**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_pr_gate_workflow.py apps/api/tests/test_api.py`
Expected: FAIL on missing service/route.

**Step 3: Implement `POST /api/pr-gate/evaluate`**

Use injected monotonic clock and cached/static inputs only. Persist one check/audit record keyed by PR
head, but keep lineage state read-only. Recheck head and environment pointer immediately before
returning.

**Step 4: Run focused tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_pr_gate_workflow.py apps/api/tests/test_api.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add packages/contracts/pr-gate-result.schema.json apps/api/src/lineage_api/application/workflows/pr_gate.py apps/api/src/lineage_api/migrations.py apps/api/src/lineage_api/api_models.py apps/api/src/lineage_api/main.py apps/api/tests/application/test_pr_gate_workflow.py apps/api/tests/test_api.py
git commit -m "feat: add bounded read-only PR gate"
```

### Task 12: Implement exact-artifact deployment promotion

**Files:**
- Create: `packages/contracts/deployment-event.schema.json`
- Create: `apps/api/src/lineage_api/application/workflows/deployment.py`
- Modify: `apps/api/src/lineage_api/migrations.py`
- Modify: `apps/api/src/lineage_api/api_models.py`
- Modify: `apps/api/src/lineage_api/main.py`
- Create: `apps/api/tests/application/test_deployment_workflow.py`

**Step 1: Write failing deployment-order tests**

Cover success, failure with no pointer change, explicit rollback, duplicate, older event, concurrent
promotion, exact package lookup, missing package to `LINEAGE_OUT_OF_SYNC`, and read-after-write
invariant. A merge event must not mutate deployment state.

**Step 2: Verify failures**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_deployment_workflow.py`
Expected: FAIL because deployment state/package lookup is absent.

**Step 3: Add deployment/package tables and handler**

Persist provider sequence, attempt, deployed digest, lineage package digest, graph version, state,
and audit ref. Promotion reuses `PublisherService` pointer fencing and never infers an edge.

**Step 4: Run deployment and publisher tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_deployment_workflow.py apps/api/tests/services/test_publisher.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add packages/contracts/deployment-event.schema.json apps/api/src/lineage_api/application/workflows/deployment.py apps/api/src/lineage_api/migrations.py apps/api/src/lineage_api/api_models.py apps/api/src/lineage_api/main.py apps/api/tests/application/test_deployment_workflow.py
git commit -m "feat: promote lineage by deployed artifact"
```

## Phase 3 — runtime evidence and publication recovery

### Task 13: Add runtime session and metadata-only observation contracts

**Files:**
- Create: `packages/contracts/runtime-observation.schema.json`
- Create: `packages/contracts/runtime-session-manifest.schema.json`
- Create: `apps/api/src/lineage_api/services/runtime.py`
- Modify: `apps/api/src/lineage_api/migrations.py`
- Create: `apps/api/tests/services/test_runtime.py`
- Create: `fixtures/runtime/openlineage-column-lineage.json`
- Create: `fixtures/runtime/otel-db-span.json`
- Create: `fixtures/runtime/sdk-field-mapping.json`

**Step 1: Write failing security and completeness tests**

Cover signed scope, expiry, revoke, artifact mismatch, `prod` deny, unknown/prohibited fields, values
and secrets, per-dataset sequence, duplicate observation, incomplete drain, and mechanism granularity.
Assert an OTel span cannot claim exact column lineage unless mapped by a separately approved parser
contract.

**Step 2: Verify failures**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/services/test_runtime.py apps/api/tests/test_contracts.py`
Expected: FAIL on missing contracts/service.

**Step 3: Implement session lifecycle and adapters**

Implement `GRANT -> READY -> OBSERVING -> DRAIN -> CLOSED` with `COMPLETE`, `INCOMPLETE`, `EXPIRED`,
and `REVOKED`. Parse OpenLineage facets, custom SDK records, and approved OTel attributes into a
common validated observation; retain mechanism and granularity.

**Step 4: Run focused tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/services/test_runtime.py apps/api/tests/test_contracts.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add packages/contracts/runtime-observation.schema.json packages/contracts/runtime-session-manifest.schema.json apps/api/src/lineage_api/services/runtime.py apps/api/src/lineage_api/migrations.py apps/api/tests/services/test_runtime.py fixtures/runtime
git commit -m "feat: validate metadata-only runtime lineage"
```

### Task 14: Bind runtime evidence to Baseline and Incremental without blocking them

**Files:**
- Modify: `apps/api/src/lineage_api/application/workflows/baseline.py`
- Modify: `apps/api/src/lineage_api/application/workflows/incremental.py`
- Modify: `apps/api/src/lineage_api/services/consolidation.py`
- Create: `apps/api/tests/application/test_runtime_reconciliation.py`
- Modify: `apps/api/tests/services/test_consolidation.py`

**Step 1: Write failing reconciliation tests**

Assert missing runtime completes deterministic flow without a false runtime claim; complete matching
session corroborates at its granularity; incomplete/mismatched session cannot promote; late session
creates a bounded re-merge and successor proposal; replay creates no duplicate provenance.

**Step 2: Verify failures**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_runtime_reconciliation.py`
Expected: FAIL because the current fixture is injected unconditionally.

**Step 3: Replace unconditional fixture behavior with the runtime port**

Keep the demo fixture behind an explicit adapter. Record skipped/missing/incomplete session status in
coverage. Late accepted evidence publishes only through normal proposal/review policy.

**Step 4: Run focused and walking-skeleton tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/application/test_runtime_reconciliation.py apps/api/tests/services/test_consolidation.py tests/test_walking_skeleton.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/application/workflows/baseline.py apps/api/src/lineage_api/application/workflows/incremental.py apps/api/src/lineage_api/services/consolidation.py apps/api/tests/application/test_runtime_reconciliation.py apps/api/tests/services/test_consolidation.py
git commit -m "refactor: reconcile optional runtime evidence"
```

### Task 15: Make publication resumable across every crash boundary

**Files:**
- Modify: `apps/api/src/lineage_api/services/publisher.py`
- Modify: `apps/api/src/lineage_api/migrations.py`
- Modify: `apps/api/tests/services/test_publisher.py`
- Create: `apps/api/tests/faults/test_publication_recovery.py`

**Step 1: Add failing crash-table tests**

Parameterize failure after manifest write, reservation, namespace creation, each edge batch, verify,
pointer transaction, and audit/outbox delivery. Redrive must activate at most one version, return the
same result when already active, discard/retain staging by policy, and never lose the approval link.

**Step 2: Verify at least one boundary cannot resume today**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/faults/test_publication_recovery.py`
Expected: FAIL before publication operation state exists.

**Step 3: Persist publication operation state**

Add operation ID, proposal/package digest, stage, token, expected prior, namespace, checksums, and
terminal outcome. Make `publish` a resumable coordinator over existing reserve/stage/activate
operations. Outbox audit/cache invalidation follows pointer activation transactionally.

**Step 4: Run publisher and fault tests**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/services/test_publisher.py apps/api/tests/faults/test_publication_recovery.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/publisher.py apps/api/src/lineage_api/migrations.py apps/api/tests/services/test_publisher.py apps/api/tests/faults/test_publication_recovery.py
git commit -m "feat: make fenced publication resumable"
```

## Phase 4 — operations, product surfaces, infrastructure, and handoff

### Task 16: Add correlation, degradation, and queue/coverage observability

**Files:**
- Create: `apps/api/src/lineage_api/observability.py`
- Modify: `apps/api/src/lineage_api/main.py`
- Modify: `apps/api/src/lineage_api/services/orchestration.py`
- Modify: `apps/api/src/lineage_api/services/query.py`
- Modify: `apps/web/src/pages/OperationsPage.tsx`
- Modify: `apps/web/src/components/operations/FlowRail.tsx`
- Modify: `apps/web/src/components/operations/GateCard.tsx`
- Test: `apps/api/tests/test_api.py`
- Test: `apps/web/src/pages/workspaces.test.tsx`

**Step 1: Write failing API/UI tests**

Require oldest queue age, saturation, retry/DLQ, lease steals, incomplete coverage, runtime join rate,
stale baseline, approval age, publish lag, pointer/package mismatch, projection watermark,
replication status placeholder, error-budget burn and unit-cost placeholder. UI must label stale,
incomplete, out-of-sync and degraded states without color alone.

**Step 2: Verify failures**

Run: `npm test --workspace apps/web -- --run src/pages/workspaces.test.tsx`
Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/test_api.py`
Expected: FAIL on missing operational fields and labels.

**Step 3: Implement a local metrics snapshot port**

Use database-derived metrics and explicit `notConfigured` for production-only signals. Never claim
RPO, availability, or cost success from a placeholder.

**Step 4: Run API/UI tests and build**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/test_api.py`
Run: `npm test --workspace apps/web -- --run src/pages/workspaces.test.tsx`
Run: `npm run build --workspace apps/web`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/observability.py apps/api/src/lineage_api/main.py apps/api/src/lineage_api/services/orchestration.py apps/api/src/lineage_api/services/query.py apps/api/tests/test_api.py apps/web/src/pages/OperationsPage.tsx apps/web/src/components/operations apps/web/src/pages/workspaces.test.tsx
git commit -m "feat: expose lineage resilience signals"
```

### Task 17: Create build-ready B01-B16 PRDs

**Files:**
- Create: `docs/build-prds/README.md`
- Create: `docs/build-prds/B01-contracts-and-correlation.md` through `docs/build-prds/B16-platform-iac-and-delivery.md`
- Modify: `tests/test_documentation.py`
- Modify: `docs/component-prds/16-delivery-plan-and-dependencies.md`

**Step 1: Write failing PRD-completeness tests**

For every Bxx file require headings for ownership, boundary, contracts, state/failure, data,
infrastructure bill of materials, local adapter, security/privacy, SLOs, observability, acceptance,
deployment/rollback, dependencies, Definition of Ready, and Definition of Done. Require at least one
`Bxx-AC-` row and links to owning Lxx requirements.

**Step 2: Verify missing-file failures**

Run: `uv run --project apps/api --extra dev pytest -q tests/test_documentation.py`
Expected: FAIL on missing B01.

**Step 3: Author PRDs in dependency-safe groups**

Write B01-B05, run the test; B06-B10, run the test; B11-B16, run the test. Use the approved design's
resource mappings and acceptance criteria. Do not duplicate domain behavior or invent CTX values.

**Step 4: Run documentation tests**

Run: `uv run --project apps/api --extra dev pytest -q tests/test_documentation.py`
Expected: PASS with all 16 PRDs accounted.

**Step 5: Commit**

```bash
git add docs/build-prds tests/test_documentation.py docs/component-prds/16-delivery-plan-and-dependencies.md
git commit -m "docs: add build-ready component PRDs"
```

### Task 18: Build deployable AWS runtime packages and CDK stack boundaries

**Files:**
- Create: `infra/package.json`
- Create: `infra/tsconfig.json`
- Create: `infra/bin/lineage-platform.ts`
- Create: `infra/lib/config.ts`
- Create: `infra/lib/runtime-assets.ts`
- Create: `infra/lib/network-stack.ts`
- Create: `infra/lib/data-stack.ts`
- Create: `infra/lib/intake-stack.ts`
- Create: `infra/lib/orchestration-stack.ts`
- Create: `infra/lib/engines-stack.ts`
- Create: `infra/lib/runtime-stack.ts`
- Create: `infra/lib/publication-stack.ts`
- Create: `infra/lib/api-stack.ts`
- Create: `infra/lib/operations-stack.ts`
- Create: `infra/test/stacks.test.ts`
- Create: `infra/test/runtime-assets.test.ts`
- Create: `infra/assets/lambda/Dockerfile`
- Create: `infra/assets/sca/Dockerfile`
- Create: `apps/api/src/lineage_api/entrypoints/aws/__init__.py`
- Create: `apps/api/src/lineage_api/entrypoints/aws/intake.py`
- Create: `apps/api/src/lineage_api/entrypoints/aws/control_stage.py`
- Create: `apps/api/src/lineage_api/entrypoints/aws/runtime_validation.py`
- Create: `apps/api/src/lineage_api/entrypoints/aws/consolidation.py`
- Create: `apps/api/src/lineage_api/entrypoints/aws/coverage.py`
- Create: `apps/api/src/lineage_api/entrypoints/aws/proposal.py`
- Create: `apps/api/src/lineage_api/entrypoints/aws/publication.py`
- Create: `apps/api/src/lineage_api/entrypoints/aws/deployment.py`
- Create: `apps/api/tests/entrypoints/aws/test_handler_contracts.py`
- Modify: `apps/api/pyproject.toml`
- Modify: `package.json`
- Modify: `Makefile`

Task numbers are not deployment units. Keep one versioned Python domain/application package, but
produce independently configurable compute targets: intake, lightweight control stages, runtime
validation, consolidation, coverage, proposal, publication, and deployment promotion Lambdas, plus
an SCA Fargate task. A shared image is acceptable, but each Lambda has its own handler, IAM role,
reserved concurrency, timeout, memory, alarms, and deployment alias. No handler imports the local
SQLite composition root.

**Step 1: Write failing handler packaging and CDK assertion tests**

Require encryption, block-public-access, versioning/Object Lock on truth buckets, PITR/deletion
protection on control tables, DLQ per lane, FIFO on interactive/events, EventBridge archive,
Standard Step Functions, reserved interactive concurrency, Kinesis encryption, Neptune multi-AZ,
alarms, tags, and no wildcard production IAM actions. Require each Python handler module to import
in a Lambda-like environment, expose a `handler(event, context)` entry point, validate a versioned
S3-reference envelope, return only bounded references, and construct dependencies through ports.
Assert that CDK creates distinct functions/roles for the handler boundaries and an ECS task
definition for SCA rather than deploying the FastAPI/local worker as one Lambda.

**Step 2: Install locked infrastructure dependencies and verify red tests**

Run: `npm install --workspace infra`
Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/entrypoints/aws/test_handler_contracts.py`
Run: `npm test --workspace infra -- --run`
Expected: FAIL because handler entry points, runtime assets, and stacks are absent.

**Step 3: Implement handler boundaries and reproducible runtime assets**

Keep handlers thin: parse the stage envelope, create an AWS composition root, call one application
use case, and serialize a typed result/reference. Put no workflow branching or domain rules in the
entry points. Build a pinned application wheel into a shared Lambda container image and build the
SCA worker as a separately bounded, non-root Fargate image with parser/rule assets. Record source
revision, dependency-lock digest, image digest, and handler name in build metadata. Add a local
runtime-interface smoke that invokes every handler with fake ports; no AWS calls belong in this
test.

**Step 4: Implement deployable stack constructs and externalized config**

Use fixture-safe defaults only for local synth. Require explicit production context for regions,
retention, quotas, budgets, enterprise endpoints, paging destinations, and account IDs. This task
must define the deployable resources, assets, least-privilege role boundaries, aliases/canary
settings, log retention, alarms, private networking, and stack outputs consumed by workflow wiring.
Local synth must not contact AWS, while `cdk deploy` with an explicit environment must have no
placeholder resource or `NotConfigured` runtime path.

**Step 5: Run packaging, synth, tests, and root build**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/entrypoints/aws/test_handler_contracts.py`
Run: `npm test --workspace infra -- --run`
Run: `npm run synth --workspace infra`
Run: `npm run package --workspace infra`
Run: `npm run build`
Expected: PASS; handler imports and fake-port invocations pass, image/wheel manifests are
reproducible, and synth emits deployable templates without contacting an AWS account.

**Step 6: Self-review the deployment bill of materials**

Compare synthesized Lambda functions, ECS task definitions, IAM roles, queues, tables, buckets,
state machines, networking, alarms, and outputs with B03-B16 and the L14 infrastructure mapping.
Fail the task if any compute target has wildcard data-plane access, if a stage has no deployment
target, or if the only runnable target is still the local FastAPI/worker process.

**Step 7: Commit**

```bash
git add infra apps/api/src/lineage_api/entrypoints/aws apps/api/tests/entrypoints/aws apps/api/pyproject.toml package.json package-lock.json Makefile
git commit -m "infra: package deployable lineage runtime units"
```

### Task 19: Wire production AWS adapters and executable Step Functions workflows

**Files:**
- Create: `apps/api/src/lineage_api/infrastructure/aws/__init__.py`
- Create: `apps/api/src/lineage_api/infrastructure/aws/config.py`
- Create: `apps/api/src/lineage_api/infrastructure/aws/composition.py`
- Create: `apps/api/src/lineage_api/infrastructure/aws/dynamodb_control.py`
- Create: `apps/api/src/lineage_api/infrastructure/aws/s3_artifacts.py`
- Create: `apps/api/src/lineage_api/infrastructure/aws/sqs_broker.py`
- Create: `apps/api/src/lineage_api/infrastructure/aws/kinesis_runtime.py`
- Create: `apps/api/src/lineage_api/infrastructure/aws/neptune_projection.py`
- Create: `scripts/export_workflow_definitions.py`
- Create: `infra/workflows/generated/workflow-contracts.json`
- Create: `infra/workflows/baseline.asl.json`
- Create: `infra/workflows/incremental.asl.json`
- Create: `infra/workflows/pr-gate.asl.json`
- Create: `infra/workflows/nightly.asl.json`
- Create: `apps/api/tests/infrastructure/aws/test_adapter_contracts.py`
- Create: `apps/api/tests/infrastructure/aws/test_composition.py`
- Create: `apps/api/tests/entrypoints/aws/test_stage_handlers.py`
- Create: `infra/test/workflows.test.ts`
- Create: `tests/aws/test_ephemeral_workflow.py`
- Create: `scripts/deploy_ephemeral_aws.sh`
- Create: `scripts/smoke_ephemeral_aws.sh`
- Modify: `infra/lib/orchestration-stack.ts`
- Modify: `infra/lib/engines-stack.ts`
- Modify: `infra/lib/publication-stack.ts`
- Modify: `infra/lib/operations-stack.ts`
- Modify: `package.json`
- Modify: `Makefile`

**Step 1: Write failing AWS adapter, composition, and handler tests**

Use fake SDK clients, not AWS calls. Require every adapter to implement its application Protocol,
perform the real conditional expression/transaction/request construction, and translate throttling,
conditional-conflict, missing-object, stale-fence, and retryable service failures into typed domain
errors. Require the AWS composition root to fail fast on missing resource identifiers; deployed
handlers may not continue with local adapters or `NotConfigured` placeholders.

**Step 2: Verify failures**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/infrastructure/aws/test_adapter_contracts.py`
Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/infrastructure/aws/test_composition.py apps/api/tests/entrypoints/aws/test_stage_handlers.py`
Expected: FAIL on absent production adapters and composition.

**Step 3: Implement concrete AWS adapters and composition**

Implement DynamoDB conditional receipt/command/lease/stage/outbox/pointer operations, versioned S3
artifact reads and immutable writes, SQS FIFO/Standard lane semantics and redrive metadata, Kinesis
partitioning/session reconciliation, and idempotent Neptune projection writes. Inject SDK clients so
unit tests remain hermetic. The composition root receives physical resource identifiers from CDK
environment variables and supplies the same ports used by local Task 9 execution. Keep large
payloads in S3 and pass exact bucket/key/version/checksum references.

**Step 4: Export Task 8 definitions and write failing workflow-parity tests**

Export the version, stage IDs, timeouts, attempts, retry class, side-effect mode, determinants,
success/error routes, and terminal states from
`apps/api/src/lineage_api/application/workflows/definitions.py` into the generated workflow contract.
The exporter supports `--check` and fails on checked-in drift. Validate every ASL definition against
that contract: Standard workflow type, complete Task/Catch/terminal topology, bounded retries and
Map concurrency, S3-reference payloads, per-stage handler/ECS target, and explicit redrive outcome.
Incremental must map I1-I10 exactly; Baseline maps B1-B10 exactly; PRGate maps P1-P8 exactly;
Nightly maps N1-N6 exactly. The dedicated deployment-promotion Lambda must execute and checkpoint
D1-D6 in order, using the same exported contract, without source-merge activation; it is not a
fifth Step Functions workflow.

**Step 5: Verify workflow tests fail before ASL and CDK wiring exist**

Run: `uv run --project apps/api python scripts/export_workflow_definitions.py --check`
Run: `npm test --workspace infra -- --run test/workflows.test.ts`
Expected: FAIL on absent generated contracts, ASL states, targets, or stack wiring.

**Step 6: Implement executable ASL and connect every task target**

Generate the workflow contract, implement the four Step Functions definitions plus the dedicated
deployment-promotion Lambda, and wire them to the distinct Lambda/Fargate targets from Task 18.
Configure Step Functions logging/tracing, execution roles,
timeouts, Retry/Catch policies, callback/heartbeat where required, bounded Baseline distributed-map
concurrency, aliases/versions, and S3-reference-only state. EventBridge/SQS routing starts the exact
workflow alias and records provider event, correlation, causation, artifact, environment, and
workflow versions. An in-flight execution remains pinned to its original state-machine and handler
versions during rollout.

**Step 7: Run hermetic adapter, handler, topology, synth, and package gates**

Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/infrastructure/aws/test_adapter_contracts.py`
Run: `uv run --project apps/api --extra dev pytest -q apps/api/tests/infrastructure/aws/test_composition.py apps/api/tests/entrypoints/aws/test_stage_handlers.py`
Run: `uv run --project apps/api python scripts/export_workflow_definitions.py --check`
Run: `npm test --workspace infra -- --run`
Run: `npm run synth --workspace infra`
Run: `npm run package --workspace infra`
Expected: PASS with no placeholder integrations, topology drift, wildcard production IAM, local
adapter import, unbounded retry/map, or unpackaged task target.

**Step 8: Deploy and smoke an ephemeral AWS environment**

Run: `AWS_PROFILE=<approved-profile> AWS_REGION=<approved-region> ./scripts/deploy_ephemeral_aws.sh`
Run: `AWS_PROFILE=<approved-profile> AWS_REGION=<approved-region> ./scripts/smoke_ephemeral_aws.sh`
Run: `AWS_PROFILE=<approved-profile> AWS_REGION=<approved-region> uv run --project apps/api --extra dev pytest -q tests/aws/test_ephemeral_workflow.py`
Expected: a clean namespaced deployment succeeds; a signed seeded event traverses SQS and the
Incremental state machine; I1-I10 use the expected Lambda/Fargate targets; DynamoDB contains one
completed command and ten immutable stage results; S3 contains checksummed evidence/coverage; a
duplicate event creates no second effect; CloudWatch contains the correlation ID; and stack outputs
identify the exact deployed image and state-machine versions. The smoke script must not publish or
advance a production pointer.

If no approved AWS account/credentials are available, retain the hermetic evidence with outcome
`AWS_REQUIRED`. Do not report `B16-AC-001` or AWS deployability as passed until this step succeeds.
Use only the explicit, namespaced cleanup command printed by the deploy script; never target an
unresolved account, region, stack prefix, or shared environment.

**Step 9: Commit**

```bash
git add apps/api/src/lineage_api/infrastructure/aws apps/api/src/lineage_api/entrypoints/aws apps/api/tests/infrastructure/aws apps/api/tests/entrypoints/aws scripts/export_workflow_definitions.py scripts/deploy_ephemeral_aws.sh scripts/smoke_ephemeral_aws.sh tests/aws infra package.json Makefile
git commit -m "feat: deploy executable lineage workflows on aws"
```

### Task 20: Build the fault, load, and acceptance-evidence harness

**Files:**
- Create: `apps/api/src/lineage_api/testing/faults.py`
- Create: `apps/api/src/lineage_api/testing/evidence.py`
- Create: `tests/acceptance/test_replay_and_faults.py`
- Create: `tests/acceptance/test_lane_fairness.py`
- Create: `tests/acceptance/test_nfr_smoke.py`
- Create: `scripts/run_acceptance.sh`
- Modify: `package.json`
- Modify: `Makefile`

**Step 1: Write failing acceptance-evidence tests**

Require each scenario to emit a schema-valid `AcceptanceEvidenceManifest` with exact artifact digest,
test ID, outcome, metrics/checksums and evidence refs. Add bounded local smoke thresholds; label the
12-hour/100-per-second/10k-burst and DR targets `AWS_REQUIRED` unless run in the approved environment.

**Step 2: Verify failures**

Run: `uv run --project apps/api --extra dev pytest -q tests/acceptance`
Expected: FAIL because the harness is absent.

**Step 3: Implement deterministic failpoints and evidence writer**

Failpoints are named boundaries, not random sleeps. Evidence writes under generated `data/acceptance`
locally and is gitignored. The runner exits nonzero for `FAIL`; `AWS_REQUIRED` is explicit and never
reported as a pass.

**Step 4: Run acceptance smoke and full verification**

Run: `make acceptance-smoke`
Run: `make verify`
Expected: acceptance local scenarios PASS, AWS-only claims reported `AWS_REQUIRED`, 0 test failures,
and frontend build exit 0.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/testing tests/acceptance scripts/run_acceptance.sh package.json Makefile
git commit -m "test: add lineage resilience acceptance harness"
```

### Task 21: Reconcile documentation, diagrams, and operator workflow

**Files:**
- Modify: `README.md`
- Modify: `docs/prototype-coverage.md`
- Modify: `docs/prd-ambiguities.md`
- Modify: `docs/plans/2026-08-05-lineage-collection-architecture-refactor-design.md`
- Modify: `tests/test_documentation.py`
- Modify: `/tmp/refactor-lineagecollector.md` outside Git

**Step 1: Write failing documentation assertions**

Require documented setup, worker/drain command, acceptance command, workflow trigger table, local/AWS
mapping, generated-state safety, failure-injection command, current coverage state, and links to the
design, plan, acceptance spec, build PRDs, and diagrams.

**Step 2: Verify failure**

Run: `uv run --project apps/api --extra dev pytest -q tests/test_documentation.py`
Expected: FAIL on missing new commands/links.

**Step 3: Update operator and architecture documentation**

Make the checked-in Mermaid source normative. Record remaining CTX values, the publication-SLO
interpretation, AWS-required evidence, and exact recovery limitations. Do not label an unexecuted
production drill complete.

**Step 4: Run final verification and inspect repository state**

Run: `make verify`
Run: `make acceptance-smoke`
Run: `git diff --check`
Run: `git status --short`
Expected: all local tests/build pass, acceptance smoke distinguishes AWS-required gates, no whitespace
errors, and only intended documentation changes remain.

**Step 5: Commit**

```bash
git add README.md docs/prototype-coverage.md docs/prd-ambiguities.md docs/plans/2026-08-05-lineage-collection-architecture-refactor-design.md tests/test_documentation.py
git commit -m "docs: hand off resilient lineage collector"
```

### Task 22: Publish the PR to main and close the automated review loop

**Files:**
- Modify only files required by actionable GitHub/Codex review threads
- Update: `/tmp/refactor-lineagecollector.md` outside Git after every review cycle

**Step 1: Verify publish prerequisites and scope**

Run: `git status --short --branch`
Run: `git diff main...HEAD --stat`
Run: `gh --version`
Run: `gh auth status`
Expected: intended branch only, clean worktree, GitHub CLI installed and authenticated. Do not stage
or publish unrelated user changes.

**Step 2: Run the final local gates**

Run: `make verify`
Run: `make acceptance-smoke`
Expected: all local tests/build pass; AWS-only claims remain explicitly `AWS_REQUIRED` unless their
approved environment evidence exists.

**Step 3: Push and open a draft PR targeting main**

Run: `git push -u origin codex/lineage-prototype`
Open a draft pull request with base `main`, head `codex/lineage-prototype`, a complete change/risk/
verification summary, and links to the architecture, plan, acceptance specification, and build PRDs.
Do not enable or invoke CodeRabbit.

**Step 4: Monitor checks and automated reviews periodically**

Poll CI plus GitHub and Codex bot reviews until checks and review threads reach a stable terminal
state. Read thread-aware review state, including unresolved/outdated status and inline anchors; flat
comment lists are not sufficient.

**Step 5: Address every actionable comment with TDD**

For each actionable thread: reproduce the concern with a failing test or documentation assertion,
verify the red phase, implement the smallest safe fix, run focused plus full required gates, perform
a local diff review, commit with a traceable message, push, reply with evidence, and resolve the
thread. Group duplicate comments; answer informational comments without forcing code. Surface
conflicting, ambiguous, or regression-causing feedback before changing behavior.

**Step 6: Repeat until satisfactory and make the PR ready**

Continue polling after each push because bot reviews may be regenerated. Completion requires all CI
checks green, no unresolved actionable GitHub/Codex bot threads, replies/evidence on addressed
comments, a clean worktree, and current acceptance evidence. Mark the draft ready for review and
record the PR URL, final head commit, checks, review disposition, and any non-actionable comments in
the progress journal.

## Final acceptance checklist

- Existing walking-skeleton behavior remains covered and compatible.
- Durable receipt and command are atomic; duplicate acceptance returns the original command.
- Lease expiry and stale epochs cannot double-complete a stage.
- Outbox delivery and workflow redrive converge after every named crash boundary.
- Coverage manifests prevent false completeness.
- Baseline is deterministic and Incremental equals Baseline on the affected scope.
- PRGate is current, bounded, read-only, and fails open only as an explicit `WARN`.
- Deployment promotion uses the exact deployed artifact and handles missing packages visibly.
- OpenLineage, custom SDK, and OTel evidence retain distinct semantics and metadata-only enforcement.
- Publication is resumable and protected by expected-prior plus monotonic fence.
- B01-B16 PRDs map behavior, infrastructure, local adapter, acceptance, rollout and recovery.
- CDK synthesizes and its assertions cover security, queues, workflows, data, projection, alarms and
  externalized configuration.
- Local acceptance evidence is retained; production-only scale/AZ/DR gates remain explicitly
  `AWS_REQUIRED` until executed in the approved environment.
- `make verify` and `make acceptance-smoke` pass before final handoff.
- A PR targets `main`; CI is green; all actionable GitHub and Codex bot review threads are addressed,
  evidenced, replied to, and resolved; CodeRabbit was not used.
