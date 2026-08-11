# Production Runtime Lineage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver an opt-in production runtime-lineage plane with configurable SDK, OTel,
OpenLineage/Spark and Dask collection, truthful completeness, ATDD/deployment gates, runtime-triggered
Incremental reconciliation and SCA-equivalent conformance evidence.

**Architecture:** Keep the existing local API/worker deployable and add a versioned runtime profile
and lease control plane, a non-blocking producer core, mechanism-specific adapters and integration
kits. Normalize every source through one closed metadata-only contract, persist raw and normalized
checksums, and close bounded observation windows with explicit loss/unsupported coverage. Use SQLite
and in-process transports locally; map the same ports to DynamoDB, Kinesis, S3, AppConfig and private
AWS intake in production.

**Tech Stack:** Python 3.12, FastAPI, SQLite, JSON Schema, pytest, OpenTelemetry Collector
configuration, OpenLineage JSON, optional Dask Distributed integration, Docker-gated Spark
OpenLineage conformance, TypeScript AWS CDK, DynamoDB, Kinesis, S3 and AppConfig.

---

## Delivery rules

- Use strict red-green-refactor for every behavior change.
- Run focused tests after each red/green cycle and the full backend suite before every task commit.
- Review every task diff for metadata leakage, silent loss, unsupported-version guessing, business
  path blocking and false completeness.
- Record RED, GREEN, review findings, verification and commit in
  `/tmp/refactor-lineagecollector.md`.
- Do not use CodeRabbit.
- Local absence of Java, Go, Spark, Dask or an enterprise collector is reported as
  `INTEGRATION_REQUIRED`, never `PASS`. Docker/Dask integration gates may be enabled explicitly.

### Task R1: Version runtime profiles, production leases and kill-switch policy

**Files:**

- Create: `packages/contracts/runtime-instrumentation-profile.schema.json`
- Create: `packages/contracts/runtime-lease.schema.json`
- Create: `packages/contracts/runtime-window-manifest.schema.json`
- Create: `apps/api/src/lineage_api/services/runtime_policy.py`
- Modify: `apps/api/src/lineage_api/migrations.py`
- Modify: `apps/api/src/lineage_api/contracts.py`
- Test: `apps/api/tests/services/test_runtime_policy.py`
- Test: `apps/api/tests/test_contracts.py`

**Step 1: Write failing contract and policy tests**

Cover:

- closed profile schema with immutable version, owner, workload, environment class, mechanism,
  install mode, framework range, dataset/attribute scope, artifact strategy, permitted granularity,
  parser contracts, buffer/overhead budget, deployment criticality and package digest;
- production disabled by default;
- an approved production profile plus exact artifact and workload identity issues a short-lived lease;
- wrong identity, artifact, environment, dataset, mechanism or unapproved profile is rejected;
- kill switches compose at global, environment, workload, mechanism and profile scope;
- disabling stops new leases, revokes active leases and records an audited reason;
- ATDD policy remains independent from production policy;
- profile identity/version is content-addressed and conflicting reuse is rejected;
- window outcomes include `COMPLETE`, `INCOMPLETE`, `EXPIRED`, `REVOKED` and `DISABLED` with attempted,
  accepted, rejected, duplicate, retried, buffered, dropped, quarantined and drained counters.

**Step 2: Verify RED**

Run:

```bash
uv run --project apps/api pytest apps/api/tests/services/test_runtime_policy.py apps/api/tests/test_contracts.py -q
```

Expected: fail because schemas, migration and `RuntimePolicyService` do not exist.

**Step 3: Implement the minimal control plane**

Add SQLite tables for immutable profiles, kill-switch state, leases and policy audit. Implement
`register_profile`, `enable_profile`, `issue_lease`, `renew_lease`, `validate_lease`, `disable`,
`enable` and `active_leases`. Use a deterministic clock, exact digests, transactional conditional
writes and domain error codes. Production can issue a lease only after explicit enablement; all
other states fail closed.

**Step 4: Verify GREEN and regressions**

Run the focused command, then:

```bash
uv run --project apps/api pytest -q
git diff --check
```

Expected: focused and full backend suites pass; no whitespace errors.

**Step 5: Self-review and commit**

Review schema closure, lease expiry, kill-switch precedence, replay/conflict handling, audit coverage
and production-default behavior. Commit:

```bash
git add packages/contracts apps/api/src/lineage_api/services/runtime_policy.py \
  apps/api/src/lineage_api/migrations.py apps/api/src/lineage_api/contracts.py \
  apps/api/tests/services/test_runtime_policy.py apps/api/tests/test_contracts.py
git commit -m "feat: govern production runtime collection"
```

### Task R2: Build the non-blocking producer core and configurable Python SDK

**Files:**

- Create: `apps/api/src/lineage_api/runtime/__init__.py`
- Create: `apps/api/src/lineage_api/runtime/models.py`
- Create: `apps/api/src/lineage_api/runtime/producer.py`
- Create: `apps/api/src/lineage_api/runtime/sdk.py`
- Create: `apps/api/src/lineage_api/runtime/transport.py`
- Test: `apps/api/tests/runtime/test_producer.py`
- Test: `apps/api/tests/runtime/test_sdk.py`

**Step 1: Write failing producer and SDK tests**

Cover:

- explicit `read`, `write`, `derive` and `connect` APIs with aliases and optional field mappings;
- deterministic observation identity from lease/window/workload/sequence/payload checksum;
- metadata-only validation before enqueue;
- bounded non-blocking enqueue with configurable microsecond/millisecond budget;
- deterministic batch order, retry classification, exponential backoff schedule without sleeps in
  unit tests, and idempotent resend;
- disabled/expired/revoked leases produce a cheap no-op counter and never call transport;
- buffer overflow, retry exhaustion and transport rejection update exact counters;
- drain closes complete only when all accepted producer records are acknowledged;
- in-process crash/restart reports lost volatile records rather than claiming complete;
- SDK never resolves catalog identity or assigns confidence.

**Step 2: Verify RED**

```bash
uv run --project apps/api pytest apps/api/tests/runtime/test_producer.py \
  apps/api/tests/runtime/test_sdk.py -q
```

Expected: import failure for the new runtime package.

**Step 3: Implement minimal producer ports and SDK**

Define `RuntimeTransport` and `LeaseProvider` protocols. Implement a bounded deque producer with an
injectable clock/scheduler, deterministic IDs, batch flush and truthful `ProducerSnapshot`. Implement
the configurable SDK as a thin metadata builder over the producer; forbid arbitrary attributes and
values.

**Step 4: Verify GREEN and regressions**

Run focused tests, the full backend suite and `git diff --check`.

**Step 5: Self-review and commit**

Review blocking behavior, queue bounds, secret/value rejection, stable identity, counter arithmetic,
retry storms and exception containment. Commit:

```bash
git add apps/api/src/lineage_api/runtime apps/api/tests/runtime
git commit -m "feat: add bounded runtime lineage sdk"
```

### Task R3: Normalize OTel and OpenLineage without semantic inflation

**Files:**

- Create: `apps/api/src/lineage_api/runtime/adapters/__init__.py`
- Create: `apps/api/src/lineage_api/runtime/adapters/otel.py`
- Create: `apps/api/src/lineage_api/runtime/adapters/openlineage.py`
- Create: `fixtures/runtime/otel/`
- Create: `fixtures/runtime/openlineage/`
- Modify: `apps/api/src/lineage_api/services/runtime.py`
- Test: `apps/api/tests/runtime/test_otel_adapter.py`
- Test: `apps/api/tests/runtime/test_openlineage_adapter.py`
- Test: `apps/api/tests/services/test_runtime.py`

**Step 1: Write failing adapter tests**

OTel cases:

- OTLP/HTTP JSON resource spans preserve service, trace and span correlation;
- normal `db.system.name`, `db.namespace`, `db.collection.name`, messaging and HTTP attributes can
  produce connectivity or approved dataset evidence only;
- generic spans never produce element evidence;
- `db.query.text`, query parameters, headers, payloads and credential-shaped attributes are removed
  before normalized evidence and cannot appear in logs/errors;
- only a profile-approved custom attribute contract can produce dataset/field mappings;
- semantic-convention/profile version mismatch is unsupported coverage, not guessed output;
- ordinary telemetry forwarding is represented by a separate port and remains unaffected when
  lineage mapping drops a span.

OpenLineage cases:

- START/RUNNING/COMPLETE/FAIL identity and parent-run facets survive normalization;
- multiple inputs, outputs and column mappings are normalized deterministically;
- absent column facets remain dataset-level;
- unknown facet versions/connectors are preserved as unsupported coverage;
- custom artifact/profile facets require immutable schema URLs and exact lease values;
- repeated events normalize byte-identically and do not double-count.

**Step 2: Verify RED**

```bash
uv run --project apps/api pytest apps/api/tests/runtime/test_otel_adapter.py \
  apps/api/tests/runtime/test_openlineage_adapter.py apps/api/tests/services/test_runtime.py -q
```

Expected: adapter imports fail and legacy parser cannot handle multi-edge fixtures.

**Step 3: Implement pure adapters and integrate validator**

Adapters return normalized observations plus an explicit unsupported/quarantine report. Reuse the
closed metadata scanner and resolver boundary. Keep raw source bytes/checksum separate from the
normalized record. Refactor `RuntimeLineageService` to delegate mechanism parsing without changing
the durable session invariants.

**Step 4: Verify GREEN and regressions**

Run focused tests, the full backend suite and `git diff --check`.

**Step 5: Self-review and commit**

Review semantic granularity, prohibited fields, multi-output ordering, profile-version behavior,
forward compatibility and duplicate identity. Commit:

```bash
git add apps/api/src/lineage_api/runtime/adapters apps/api/src/lineage_api/services/runtime.py \
  apps/api/tests/runtime apps/api/tests/services/test_runtime.py fixtures/runtime
git commit -m "feat: normalize otel and openlineage evidence"
```

### Task R4: Add Spark and Dask integration kits and compatibility corpus

**Files:**

- Create: `apps/api/src/lineage_api/runtime/integrations/__init__.py`
- Create: `apps/api/src/lineage_api/runtime/integrations/spark.py`
- Create: `apps/api/src/lineage_api/runtime/integrations/dask.py`
- Create: `runtime-kits/spark/openlineage.yml`
- Create: `runtime-kits/spark/compatibility.json`
- Create: `runtime-kits/dask/compatibility.json`
- Create: `runtime-kits/README.md`
- Create: `apps/api/tests/runtime/test_spark_kit.py`
- Create: `apps/api/tests/runtime/test_dask_plugin.py`
- Create: `tests/integration/runtime/test_dask_live.py`
- Create: `tests/integration/runtime/test_spark_openlineage.py`
- Modify: `apps/api/pyproject.toml`
- Modify: `package.json`

**Step 1: Write failing kit tests**

Cover:

- Spark configuration composes the official OpenLineage listener with existing listeners and never
  installs a duplicate;
- transport/profile/artifact settings are exact and secrets are not written to configuration;
- supported Spark/Scala/OpenLineage/connector cells are explicit and unknown cells fail closed;
- Dask plugin registration is idempotent and uses scheduler graph annotations/IO metadata only;
- Dask values, task arguments and partitions are never serialized;
- Dask defaults to dataset granularity; exact fields require SDK annotations;
- plugin close/failure emits a truthful producer snapshot;
- live Dask and Docker Spark tests report `INTEGRATION_REQUIRED` when their explicit enable flag or
  toolchain is absent.

**Step 2: Verify RED**

```bash
uv run --project apps/api pytest apps/api/tests/runtime/test_spark_kit.py \
  apps/api/tests/runtime/test_dask_plugin.py -q
```

Expected: integration kit imports and artifacts do not exist.

**Step 3: Implement kits and gated live tests**

Generate safe Spark configuration around the official listener. Implement the Dask class so the
module imports without Dask installed while conforming to `SchedulerPlugin` when the optional extra
is present. Add a `runtime-integration` extra for Dask and OpenTelemetry protocol fixtures. Make
Docker Spark and live Dask gates opt-in and evidence-labeled.

**Step 4: Verify GREEN and available integrations**

```bash
uv run --project apps/api pytest apps/api/tests/runtime/test_spark_kit.py \
  apps/api/tests/runtime/test_dask_plugin.py -q
uv run --project apps/api pytest tests/integration/runtime -q
uv run --project apps/api pytest -q
```

Expected: deterministic kit tests pass; unavailable live cells skip as `INTEGRATION_REQUIRED`.

**Step 5: Self-review and commit**

Review duplicate listener behavior, compatibility closure, import isolation, value leakage,
framework lifecycle and skip truthfulness. Commit:

```bash
git add apps/api/src/lineage_api/runtime/integrations apps/api/tests/runtime \
  tests/integration/runtime runtime-kits apps/api/pyproject.toml apps/api/uv.lock package.json
git commit -m "feat: add spark and dask runtime kits"
```

### Task R5: Upgrade durable runtime windows and production intake

**Files:**

- Modify: `apps/api/src/lineage_api/services/runtime.py`
- Modify: `apps/api/src/lineage_api/migrations.py`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/runtime_validation.py`
- Modify: `apps/api/src/lineage_api/infrastructure/aws/kinesis_runtime.py`
- Create: `apps/api/src/lineage_api/application/runtime_intake.py`
- Create: `apps/api/src/lineage_api/entrypoints/runtime_api.py`
- Modify: `apps/api/src/lineage_api/main.py`
- Test: `apps/api/tests/application/test_runtime_intake.py`
- Test: `apps/api/tests/services/test_runtime.py`
- Test: `apps/api/tests/test_api.py`
- Test: `apps/api/tests/infrastructure/test_aws_adapters.py`

**Step 1: Write failing intake/window tests**

Cover production lease validation, raw and normalized checksums, workload/profile/window binding,
mechanism counters, quarantine/unsupported coverage, atomic idempotency, partition key, disabled
window close and concurrent kill-switch/observe ordering. Prove all legacy session behavior remains
compatible and non-production test sessions still work.

**Step 2: Verify RED**

Run the four focused test files. Expected: production leases and v2 windows are unsupported.

**Step 3: Implement application intake and APIs**

Add a durable intake application service behind FastAPI and Lambda entry points. The transaction
validates policy/lease, raw checksum, metadata, resolver scope, observation identity and ordering
before recording normalized evidence/counters. Publish only the accepted immutable reference to
Kinesis; close windows from durable counts, never client claims alone.

**Step 4: Verify GREEN and regressions**

Run focused tests, full backend tests and `git diff --check`.

**Step 5: Self-review and commit**

Review transactional races, trust boundaries, raw payload retention, partition ordering, retries,
error/log redaction and backward compatibility. Commit:

```bash
git add apps/api/src/lineage_api apps/api/tests
git commit -m "feat: ingest durable production runtime windows"
```

### Task R6: Reconcile runtime discovery and orchestration triggers

**Files:**

- Modify: `apps/api/src/lineage_api/application/runtime_reconciliation.py`
- Modify: `apps/api/src/lineage_api/services/consolidation.py`
- Modify: `apps/api/src/lineage_api/application/incremental_workflow.py`
- Modify: `apps/api/src/lineage_api/application/baseline_workflow.py`
- Modify: `apps/api/src/lineage_api/application/pr_gate_workflow.py`
- Modify: `apps/api/src/lineage_api/application/deployment_workflow.py`
- Test: `apps/api/tests/application/test_runtime_reconciliation.py`
- Test: `apps/api/tests/application/test_incremental_workflow.py`
- Test: `apps/api/tests/application/test_baseline_workflow.py`
- Test: `apps/api/tests/application/test_pr_gate_workflow.py`
- Test: `apps/api/tests/application/test_deployment_workflow.py`

**Step 1: Write failing orchestration tests**

Cover:

- complete exact SDK/OpenLineage evidence can create one reviewed runtime-discovery proposal when SCA
  has no edge;
- connectivity-only OTel evidence cannot create an exact or publishable edge;
- contradictory evidence preserves both sources and requires review;
- complete new manifest/checksum triggers one Incremental command; replay is a no-op;
- incomplete/disabled manifests update coverage and never trigger confidence promotion;
- Baseline consumes eligible historical windows without waiting or enabling collection;
- PRGate remains read-only and never grants a lease or waits for runtime;
- Deployment requires the exact ATDD profile/artifact manifest for critical workloads, then records
  canary enable/disable outcome without taking down the workload.

**Step 2: Verify RED**

Run the six focused application test files. Expected: runtime-only discovery and gates fail.

**Step 3: Implement minimal reconciliation and trigger behavior**

Use deterministic provenance IDs and durable command idempotency. Route runtime discoveries through
normal proposal/review/publication. Add runtime coverage references to workflow manifests without
making runtime a synchronous dependency of Baseline, Incremental or PRGate.

**Step 4: Verify GREEN and regressions**

Run focused tests, full backend tests, workflow export/check and `git diff --check`.

**Step 5: Self-review and commit**

Review confidence inflation, trigger loops, evidence arrival order, PRGate writes, deployment safety
and replay behavior. Commit:

```bash
git add apps/api/src/lineage_api/application apps/api/src/lineage_api/services/consolidation.py \
  apps/api/tests/application
git commit -m "feat: orchestrate runtime lineage reconciliation"
```

### Task R7: Package the OTel path and AWS production controls

**Files:**

- Create: `runtime-kits/otel/otel-collector.yaml`
- Create: `runtime-kits/otel/Dockerfile`
- Create: `runtime-kits/otel/compatibility.json`
- Modify: `infra/lib/runtime-stack.ts`
- Modify: `infra/lib/runtime-assets.ts`
- Modify: `infra/lib/intake-stack.ts`
- Modify: `infra/lib/operations-stack.ts`
- Modify: `infra/lib/config.ts`
- Modify: `infra/test/runtime-assets.test.ts`
- Modify: `infra/test/stacks.test.ts`
- Create: `infra/test/runtime-controls.test.ts`
- Modify: `scripts/deploy_ephemeral_aws.sh`
- Modify: `scripts/smoke_ephemeral_aws.sh`
- Modify: `scripts/cleanup_ephemeral_aws.sh`

**Step 1: Write failing infrastructure tests**

Assert:

- stock Collector distribution uses isolated normal-telemetry and lineage pipelines;
- lineage path removes prohibited query/parameter/header attributes before export;
- private runtime intake, profile/lease/kill-switch stores, KMS, alarms, DLQ/quarantine and S3 raw/
  normalized/window prefixes exist;
- production starts disabled and requires explicit profile/workload enablement;
- Lambda IAM cannot bypass policy or write evidence without the intake transaction;
- canary, buffer/dropped, incomplete-window and kill-switch alarms exist;
- ephemeral deploy/smoke/cleanup exercises enable, observe, close, disable and cleanup safely.

**Step 2: Verify RED**

```bash
npm test --workspace infra -- --run
```

Expected: missing Collector asset and runtime controls fail assertions.

**Step 3: Implement minimal packaging and CDK**

Use a pinned stock OTel Collector image/config with standard filter/transform/batch/export components;
keep semantic normalization in the versioned runtime intake adapter. Add exact asset digests and
least-privilege AWS resources. Preserve protected production deletion/retention and disposable
`lineage-e2e-*` behavior.

**Step 4: Verify GREEN and synth**

```bash
npm test --workspace infra -- --run
npm run build --workspace infra
npm run synth --workspace infra
git diff --check
```

Expected: infrastructure tests, compile and local synth pass.

**Step 5: Self-review and commit**

Review Collector fan-out, silent sampling, sensitive attributes, IAM, default-off production,
deletion policy, exact pins and alarms. Commit:

```bash
git add runtime-kits/otel infra scripts
git commit -m "infra: deploy governed runtime lineage collection"
```

### Task R8: Prove runtime ATDD, resilience, completeness and operator handoff

**Files:**

- Create: `tests/acceptance/test_runtime_collection.py`
- Create: `tests/acceptance/test_runtime_faults.py`
- Create: `tests/acceptance/test_runtime_atdd.py`
- Modify: `scripts/run_acceptance.sh`
- Modify: `README.md`
- Modify: `docs/component-prds/06-runtime-observation-plane.md`
- Modify: `docs/build-prds/B08-runtime-session-and-ingestion.md`
- Modify: `docs/build-prds/B09-runtime-emitters-and-integrations.md`
- Modify: `docs/acceptance/lineage-platform-acceptance.md`
- Modify: `docs/prototype-coverage.md`
- Modify: `docs/prd-ambiguities.md`
- Modify: `docs/plans/2026-08-05-lineage-collection-architecture-refactor-design.md`
- Modify: `tests/test_documentation.py`

**Step 1: Write failing acceptance and documentation tests**

Scenarios must prove SDK, OTel, OpenLineage/Spark and Dask mechanism fidelity; kill switch; expiry;
revocation; network partition; retry; duplicate; reorder; collector crash/restart; overflow;
partial drain; metadata attack; exact artifact/profile ATDD gate; runtime-only reviewed proposal;
Incremental trigger; Baseline non-wait; PRGate read-only behavior; and truthful integration-required
states. Documentation tests require exact setup, ATDD, production enable/disable, rollback, evidence
inspection and compatibility commands.

**Step 2: Verify RED**

```bash
uv run --project apps/api pytest tests/acceptance/test_runtime_collection.py \
  tests/acceptance/test_runtime_faults.py tests/acceptance/test_runtime_atdd.py \
  tests/test_documentation.py -q
```

Expected: new acceptance/documentation requirements fail.

**Step 3: Implement harness and reconcile specifications**

Emit content-addressed acceptance manifests with exact source/profile/package digests, thresholds,
environment and `PASS`, `FAIL`, `AWS_REQUIRED`, `INTEGRATION_REQUIRED` or `NOT_CONFIGURED`. Replace all
remaining blanket production-deny text with the approved default-off policy. Document operators,
owners, kill switch, drain, rollback, investigation and evidence verification.

**Step 4: Run final verification**

```bash
npm run verify
make acceptance-smoke
npm run workflow:check
git diff --check
```

Also run explicitly enabled Dask/Spark/AWS gates when their toolchains and approved environments are
available. Expected: all locally available gates pass; unavailable external gates are named and do
not count as passes.

**Step 5: Final self-review and commit**

Review the approved design line by line, verify every acceptance ID has evidence, inspect generated
artifacts, confirm the worktree contains no unrelated changes, then commit:

```bash
git add tests scripts README.md docs
git commit -m "test: prove production runtime lineage controls"
```

## Completion gate

The runtime hardening track is complete only when R1–R8 are independently committed, locally
available verification is green, generated artifacts name the final source revision and exact
digests, external gates are truthfully labeled, and `/tmp/refactor-lineagecollector.md` contains the
review/evidence checkpoint for every task. Task 22 (push, PR to `main`, GitHub/Codex bot review loop)
starts only after this gate and the independently reviewed production AWS application-completion
A1–A9 track in `2026-08-08-production-aws-application-completion.md`.
