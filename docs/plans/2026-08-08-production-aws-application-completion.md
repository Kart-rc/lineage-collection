# Production AWS Application Completion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make every generated AWS workflow state execute its real lineage application behavior and
deliver the review/query UI as a secured, testable AWS product.

**Architecture:** Keep the existing domain/application package and ports as the authority. Add a
closed stage-to-functional-target registry, deploy one immutable backend image through independently
versioned Lambda/Fargate targets, and bind those targets to DynamoDB, S3, SQS, Kinesis and Neptune
adapters. Serve the React build through CloudFront and expose only authenticated API Gateway routes.

**Tech Stack:** Python 3.12, FastAPI behavioral oracle, boto3 AWS adapters, TypeScript, React/Vite,
AWS CDK, Lambda container images, ECS/Fargate, Step Functions Standard, API Gateway, S3/CloudFront,
DynamoDB, SQS, Kinesis, Neptune, Cognito/OIDC, CloudWatch, pytest and Vitest.

---

## Execution rules

- Runtime hardening R3–R8 remains a separate required track. Finish and commit the currently dirty
  R3 checkpoint before A1 production code so commits remain reviewable.
- Follow RED → GREEN → REFACTOR for every behavior change. Record the failing command and expected
  reason in `/tmp/refactor-lineagecollector.md` before writing implementation code.
- After each A-task, run its focused gates, the affected regression suite and `git diff --check`;
  self-review the staged diff; commit only explicit files; never stage the unrelated `.gitignore` or
  `second-brain/` change.
- Do not use CodeRabbit. Task 22 remains the GitHub/Codex bot review loop after R1–R8 and A1–A9.
- Generated ASL is never hand-edited. Change the Python authority and run `make workflow-export`.

### Task A1: Add closed stage ownership and a dedicated classification Lambda

**Files:**

- Create: `apps/api/src/lineage_api/application/stage_ownership.py`
- Create: `apps/api/src/lineage_api/entrypoints/aws/classification.py`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/common.py`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/control_stage.py`
- Modify: `apps/api/src/lineage_api/application/workflows/definitions.py`
- Modify: `scripts/export_workflow_definitions.py`
- Modify: `infra/lib/runtime-assets.ts`
- Modify: `infra/lib/config.ts`
- Modify: `infra/lib/data-stack.ts`
- Modify: `infra/lib/orchestration-stack.ts`
- Modify: `scripts/deploy_ephemeral_aws.sh`
- Modify: `README.md`
- Modify: `docs/build-prds/B16-platform-iac-and-delivery.md`
- Modify: `docs/prototype-coverage.md`
- Modify generated: `infra/workflows/baseline.asl.json`
- Modify generated: `infra/workflows/incremental.asl.json`
- Modify generated: `infra/workflows/pr-gate.asl.json`
- Modify generated: `infra/workflows/nightly.asl.json`
- Test: `apps/api/tests/application/test_stage_ownership.py`
- Test: `apps/api/tests/entrypoints/aws/test_stage_handlers.py`
- Test: `apps/api/tests/entrypoints/aws/test_handler_contracts.py`
- Test: `infra/test/runtime-assets.test.ts`
- Test: `infra/test/workflows.test.ts`
- Test: `infra/test/stacks.test.ts`

**Step 1: Write the failing ownership tests**

Specify a closed `owner_for_stage(workflow_kind, stage_id)` API. Assert that every stage in
`WORKFLOWS` has exactly one owner, B3 belongs to `classification`, only B3 is initially owned by that
target, D1–D6 belong to `deployment`, SCA states belong to `sca`, and unknown or workflow-mismatched
IDs raise `UnknownStageError`.

**Step 2: Write the failing handler and infrastructure tests**

Require `classification.handler` to reject a B2/B4 event before constructing the AWS executor.
Require `control_stage.handler` to reject B3. Require build metadata to list nine unique handlers,
generated B3 ASL to use `${ClassificationAliasArn}`, CDK to provision its immutable version/canary,
and production concurrency configuration to require `classification`.

**Step 3: Verify RED**

Run:

```bash
uv run --project apps/api pytest \
  apps/api/tests/application/test_stage_ownership.py \
  apps/api/tests/entrypoints/aws/test_stage_handlers.py \
  apps/api/tests/entrypoints/aws/test_handler_contracts.py -q
npm test --workspace infra -- --run runtime-assets.test.ts workflows.test.ts stacks.test.ts
```

Expected: missing `stage_ownership` and `classification` imports, eight-handler inventory and B3
still routed to `ControlAliasArn`.

**Step 4: Implement the closed registry and fail-before-I/O handler guard**

Define immutable `StageOwner` values and derive coverage against `WORKFLOWS` at import/test time.
Require `workflowKind`, `workflowVersion`, `stageId` and `stageName` for workflow-stage handlers.
`create_handler(target)` validates ownership before calling `executor_factory`. Keep deployment's
outer envelope compatible while validating each generated D-stage inside its loop.

**Step 5: Add the classification deployable target and regenerate workflows**

Add `classification` to `LAMBDA_TARGETS`, production concurrency and the orchestration stack. Pass
the immutable classification function version as `ClassificationAliasArn`. Import target ownership
into the exporter instead of maintaining a second mapping, then run:

```bash
make workflow-export
make workflow-check
```

**Step 6: Verify GREEN and regressions**

Run the focused commands from Step 3, then:

```bash
uv run --project apps/api pytest apps/api/tests/application/test_workflow_definitions.py \
  apps/api/tests/entrypoints/aws apps/api/tests/infrastructure/aws -q
npm test --workspace infra -- --run
npm run build --workspace infra
git diff --check
```

Expected: registry coverage, fail-before-I/O guards, generated parity, CDK assertions and compile all
pass. This task proves ownership/routing only; it must not claim classification business execution.

**Step 7: Review, journal and commit**

Review for duplicate mappings, workflow-version ambiguity, direct-handler bypass, deployment wrapper
compatibility, IAM overreach and generated drift. Commit:

```bash
git add apps/api/src/lineage_api/application/stage_ownership.py \
  apps/api/src/lineage_api/entrypoints/aws/common.py \
  apps/api/src/lineage_api/entrypoints/aws/control_stage.py \
  apps/api/src/lineage_api/entrypoints/aws/classification.py \
  apps/api/src/lineage_api/application/workflows/definitions.py \
  apps/api/tests/application/test_stage_ownership.py \
  apps/api/tests/entrypoints/aws scripts/export_workflow_definitions.py \
  infra/lib/runtime-assets.ts infra/lib/config.ts infra/lib/orchestration-stack.ts \
  infra/test infra/workflows
git commit -m "infra: assign workflow stages to production targets"
```

### Task A2: Bind functional Lambda targets to real application use cases

**Files:**

- Create: `apps/api/src/lineage_api/application/stage_execution.py`
- Create: `apps/api/src/lineage_api/application/stage_handlers.py`
- Modify: `apps/api/src/lineage_api/application/ports.py`
- Modify: `apps/api/src/lineage_api/infrastructure/aws/composition.py`
- Modify: `apps/api/src/lineage_api/infrastructure/aws/dynamodb_control.py`
- Modify: `apps/api/src/lineage_api/infrastructure/aws/s3_artifacts.py`
- Modify: `apps/api/src/lineage_api/infrastructure/aws/neptune_projection.py`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/classification.py`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/coverage.py`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/runtime_validation.py`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/consolidation.py`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/proposal.py`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/publication.py`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/deployment.py`
- Test: `apps/api/tests/application/test_stage_execution.py`
- Test: `apps/api/tests/infrastructure/aws/test_composition.py`
- Test: `apps/api/tests/entrypoints/aws/test_stage_handlers.py`

**Step 1: Write failing domain-output tests**

For representative B3, B4, B6, B8, B9, B10 and D1–D6 inputs, require typed domain output rather
than `{target,inputDocument}`. B3 must contain the immutable classification decision and policy
version; B4 a bounded work inventory; B6 a runtime coverage reference; B8 consolidated edge IDs and
coverage; B9 a proposal/decision; B10 a fenced pointer receipt; D6 a read-back verification receipt.

**Step 2: Verify RED**

Run the three focused test files. Expected: current `AwsStageExecutor` persists the generic stage
echo, so domain-field assertions fail.

**Step 3: Introduce the application dispatcher and ports**

Define `StageUseCase.execute(input_document, context) -> StageExecutionResult`. Inject a closed map of
stage IDs to use cases. Keep lease claim/record and immutable artifact persistence in one executor
template; place business decisions inside application use cases. Use target-specific artifact kinds
and schema versions. Unknown stage or wrong owner fails before claim.

**Step 4: Implement one real vertical slice at a time**

Implement and verify in this order: B3 classification, B4/I3 coverage, B6/I6 runtime validation,
B7/B8/I7/P4 consolidation, B9/I9/N6 proposal, B10/I10/N3 publication, then D1–D6. Reuse existing
local domain algorithms behind new ports; do not import the SQLite composition root.

**Step 5: Verify GREEN and regressions**

Run focused tests after every slice, followed by the full backend suite, workflow check and diff
check. Assert retryable AWS errors do not record success and deterministic rejection is retained as
an explicit terminal artifact.

**Step 6: Review, journal and commit**

Review trust boundaries, transaction ordering, duplicate identity, schema closure, target IAM needs,
raw-payload logging and rollback. Commit:

```bash
git add apps/api/src/lineage_api/application apps/api/src/lineage_api/infrastructure/aws \
  apps/api/src/lineage_api/entrypoints/aws apps/api/tests
git commit -m "feat: execute real lineage stages on aws"
```

### Task A3: Run deterministic SCA in the Fargate callback worker

**Files:**

- Create: `apps/api/src/lineage_api/application/sca_execution.py`
- Modify: `apps/api/src/lineage_api/infrastructure/aws/composition.py`
- Modify: `apps/api/src/lineage_api/entrypoints/sca_worker.py`
- Modify: `infra/assets/sca/Dockerfile`
- Modify: `infra/lib/engines-stack.ts`
- Test: `apps/api/tests/application/test_sca_execution.py`
- Test: `apps/api/tests/infrastructure/aws/test_composition.py`
- Test: `infra/test/runtime-assets.test.ts`

**Step 1: Write failing SCA callback tests**

Require a pinned source/artifact reference to produce deterministic SCA evidence with citations,
transforms and residue; require heartbeats during bounded work; require task failure on corrupt input,
timeout or output checksum mismatch; prove values/secrets are never emitted.

**Step 2: Verify RED**

Run focused Python and infrastructure tests. Expected: the callback currently writes a generic
stage result and never invokes the SCA engine.

**Step 3: Implement execution and heartbeat ports**

Resolve immutable input, invoke the existing deterministic SCA engine, persist exact evidence,
verify its checksum and return only a bounded reference through `SendTaskSuccess`. Run periodic
`SendTaskHeartbeat`; contain and redact failure causes sent to Step Functions.

**Step 4: Verify, review and commit**

Run focused tests, full backend tests, SCA image build/package metadata checks and diff check. Review
task-token exposure, source credential lifetime, timeout cancellation, residue truthfulness and
determinism. Commit:

```bash
git add apps/api/src/lineage_api/application/sca_execution.py \
  apps/api/src/lineage_api/infrastructure/aws/composition.py \
  apps/api/src/lineage_api/entrypoints/sca_worker.py apps/api/tests \
  infra/assets/sca/Dockerfile infra/lib/engines-stack.ts infra/test
git commit -m "feat: execute deterministic sca in fargate"
```

### Task A4: Prove a domain artifact for every AWS workflow state

**Files:**

- Create: `tests/integration/aws/test_stage_artifacts.py`
- Create: `tests/integration/aws/test_workflow_execution.py`
- Create: `fixtures/aws/workflow-inputs/`
- Modify: `apps/api/tests/infrastructure/aws/test_adapter_contracts.py`
- Modify: `scripts/run_acceptance.sh`

**Step 1: Write failing stage-matrix tests**

Generate the matrix from `WORKFLOWS` and the stage registry. For every state, execute the real use
case with deterministic fake AWS services and validate the output contract, immutable checksum,
idempotent replay, target ownership and failure behavior. Reject generic target/input echo artifacts.

**Step 2: Verify RED, implement only missing seams, then verify GREEN**

Run the integration files and observe incomplete states. Add the minimum adapters/fixtures required;
do not weaken assertions. Run full backend, workflow parity and acceptance smoke.

**Step 3: Review, journal and commit**

Review matrix completeness against generated ASL and ensure fake-AWS evidence is labeled
`LOCAL_PASS`, never live `AWS_PASS`. Commit:

```bash
git add tests/integration/aws fixtures/aws apps/api/tests/infrastructure/aws scripts/run_acceptance.sh
git commit -m "test: prove every aws workflow stage artifact"
```

### Task A5: Expose the production product and query API

**Files:**

- Create: `apps/api/src/lineage_api/application/product_api.py`
- Create: `apps/api/src/lineage_api/entrypoints/aws/product_api.py`
- Create: `apps/api/src/lineage_api/infrastructure/aws/query_projection.py`
- Modify: `infra/lib/runtime-assets.ts`
- Modify: `infra/lib/api-stack.ts`
- Modify: `infra/bin/lineage-platform.ts`
- Test: `apps/api/tests/entrypoints/aws/test_product_api.py`
- Test: `apps/api/tests/infrastructure/aws/test_query_projection.py`
- Test: `infra/test/stacks.test.ts`

**Step 1: Write failing route-parity tests**

Require production equivalents for overview, resilience, runs, proposals, approve/reject/correct,
lineage, edge detail, impact and runtime administration. Explicitly forbid demo reset/seed routes.
Assert bounded pagination, conditional mutation, immutable audit and no direct browser data access.

**Step 2: Verify RED and implement query/review application ports**

Use DynamoDB for ledgers/proposals/pointers, S3 for evidence and Neptune for graph queries. Add one
versioned product API Lambda initially; split only when measured scaling or IAM requires it. Wire API
Gateway proxy routes to its immutable alias and configure access logs without bodies.

**Step 3: Verify, review and commit**

Run focused Python/CDK tests, full backend/infra suites, build/synth and diff check. Review route auth,
pagination, traversal bounds, optimistic concurrency and error redaction. Commit:

```bash
git add apps/api/src/lineage_api/application/product_api.py \
  apps/api/src/lineage_api/entrypoints/aws/product_api.py \
  apps/api/src/lineage_api/infrastructure/aws/query_projection.py apps/api/tests \
  infra/lib/runtime-assets.ts infra/lib/api-stack.ts infra/bin/lineage-platform.ts infra/test
git commit -m "feat: expose the lineage product api on aws"
```

### Task A6: Deliver the React product through S3 and CloudFront

**Files:**

- Create: `apps/web/src/config/runtime.ts`
- Create: `infra/lib/web-stack.ts`
- Modify: `apps/web/src/api/client.ts`
- Modify: `apps/web/src/pages/OperationsPage.tsx`
- Modify: `apps/web/vite.config.ts`
- Modify: `infra/bin/lineage-platform.ts`
- Modify: `scripts/deploy_ephemeral_aws.sh`
- Test: `apps/web/src/config/runtime.test.ts`
- Test: `apps/web/src/api/client.test.ts`
- Test: `infra/test/stacks.test.ts`

**Step 1: Write failing product-delivery tests**

Require an environment-bound same-origin API base, immutable hashed assets, SPA fallback, security
headers, access logging and no demo controls outside local mode. Require CloudFront to reach only the
private/static S3 origin and API Gateway origin.

**Step 2: Verify RED and implement the web stack**

Build once, deploy hashed assets with long caching and the shell/runtime config with no-cache. Inject
only public configuration. Route `/api/*` through CloudFront to API Gateway; never ship AWS
credentials or secrets to the browser.

**Step 3: Verify, browser-smoke, review and commit**

Run frontend tests/build, infrastructure tests/build/synth and a local production-mode browser smoke.
Review caching, rollback, CSP, source maps and environment isolation. Commit:

```bash
git add apps/web infra/lib/web-stack.ts infra/bin/lineage-platform.ts infra/test \
  scripts/deploy_ephemeral_aws.sh
git commit -m "feat: deliver the lineage ui through cloudfront"
```

### Task A7: Add production identity, RBAC and security boundaries

**Files:**

- Create: `apps/api/src/lineage_api/application/authorization.py`
- Create: `infra/lib/identity-stack.ts`
- Modify: `apps/api/src/lineage_api/entrypoints/aws/product_api.py`
- Modify: `infra/lib/api-stack.ts`
- Modify: `infra/lib/web-stack.ts`
- Modify: `infra/lib/operations-stack.ts`
- Modify: `infra/lib/config.ts`
- Test: `apps/api/tests/application/test_authorization.py`
- Test: `apps/api/tests/entrypoints/aws/test_product_api.py`
- Create: `infra/test/security-controls.test.ts`

**Step 1: Write failing authorization/security tests**

Cover viewer, reviewer, publisher, operator and runtime-administrator permissions; deny missing,
expired or wrong-environment identities; require actor/correlation audit; require WAF, TLS, secret
references, encryption and no wildcard data-plane grants.

**Step 2: Verify RED and implement organization-configurable OIDC**

Use an explicit OIDC/Cognito configuration seam, API Gateway authorizer and application-level
capability checks. Never trust UI visibility as authorization. Protect mutation routes with
conditional writes and auditable actor identity.

**Step 3: Verify, security-review and commit**

Run Python/CDK tests, production synth and negative cases. Review confused deputy, group mapping,
token logging, CSRF/CORS, WAF bypass and break-glass runtime disable. Commit:

```bash
git add apps/api/src/lineage_api/application/authorization.py \
  apps/api/src/lineage_api/entrypoints/aws/product_api.py apps/api/tests \
  infra/lib infra/test
git commit -m "feat: secure lineage product actions with rbac"
```

### Task A8: Build immutable staging and production promotion

**Files:**

- Create: `.github/workflows/lineage-release.yml`
- Create: `scripts/deploy_production_aws.sh`
- Create: `scripts/verify_release_evidence.py`
- Modify: `scripts/deploy_ephemeral_aws.sh`
- Modify: `infra/lib/config.ts`
- Modify: `README.md`
- Test: `tests/test_release_pipeline.py`
- Test: `infra/test/stacks.test.ts`

**Step 1: Write failing pipeline-policy tests**

Require PR verification without deploy, immutable source/image/template/SBOM digests, staging deploy,
integration/security/runtime ATDD, manual production approval, canary alarms, alias rollback and
evidence retention. Reject mutable tags, dirty source, fixture context and missing approvals.

**Step 2: Verify RED and implement guarded promotion**

Use workload identity/OIDC rather than standing credentials. Publish images before compute stacks,
deploy additive state changes first, promote exact aliases only after gates, and retain the prior
version. Production runtime collection stays disabled unless its separate policy gate is satisfied.

**Step 3: Verify, review and commit**

Run release-policy tests, all builds/synth/package checks and dry-run validation. Review environment
protection, artifact substitution, rollback ordering, concurrency and cost guardrails. Commit:

```bash
git add .github/workflows/lineage-release.yml scripts README.md tests/test_release_pipeline.py \
  infra/lib/config.ts infra/test/stacks.test.ts
git commit -m "ci: promote immutable lineage releases"
```

### Task A9: Prove signed event to queryable graph in AWS

**Files:**

- Create: `tests/aws/test_full_product_workflow.py`
- Create: `tests/acceptance/test_production_application.py`
- Modify: `scripts/smoke_ephemeral_aws.sh`
- Modify: `scripts/run_acceptance.sh`
- Modify: `docs/acceptance/lineage-platform-acceptance.md`
- Modify: `docs/prototype-coverage.md`
- Modify: `docs/prd-ambiguities.md`
- Modify: `README.md`

**Step 1: Write the failing end-to-end acceptance scenario**

Require signed intake → workflow selection → classification → coverage → SCA → optional runtime →
consolidation → proposal → authorized approval → fenced publication → lineage/impact query. Replay
the event and approval, inject one retryable failure, and verify no duplicate evidence or pointer
advance. Verify production runtime stays off unless explicitly enabled.

**Step 2: Verify RED and complete only missing integration seams**

Run locally with deterministic AWS doubles, then run the guarded ephemeral account test only when its
explicit account/cost flags are present. Preserve `AWS_REQUIRED` for unexecuted load, availability,
security and DR cells.

**Step 3: Run final gates**

```bash
npm run verify
make workflow-check
make synth
make package-aws
make acceptance-smoke
git diff --check
```

When approved AWS context exists, additionally run `make aws-deploy` and `make aws-smoke`, retain the
content-addressed outputs and then use the separately approved cleanup command.

**Step 4: Final review, reconcile truth and commit**

Review every design claim against retained evidence, remove stale structural-complete language, list
all `AWS_REQUIRED`/`NOT_CONFIGURED` items and confirm generated artifacts name the final revision.
Commit:

```bash
git add tests scripts README.md docs
git commit -m "test: prove the production aws application workflow"
```

## Completion gate before Task 22

A1–A9 and R1–R8 must each have a journaled RED/GREEN/review/verification checkpoint and an isolated
commit. All locally available gates must pass; generated package metadata must bind the final source
revision to exact digests; unavailable external cells must remain explicit. Only then push, open the
PR to `main`, and process GitHub/Codex bot review threads. CodeRabbit remains prohibited.
