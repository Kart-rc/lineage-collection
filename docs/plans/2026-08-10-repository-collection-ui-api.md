# Repository Collection UI and API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let a user submit an exact-revision local checkout or Git repository through the React application, execute the existing durable lineage workflow, and inspect collection progress and results through the product API.

**Architecture:** Add a repository-collection application service that turns either source descriptor into the same immutable snapshot and durable orchestration request. FastAPI exposes submit and status resources; the local adapter may drive the durable command immediately while preserving an HTTP `202` polling contract. React submits the typed request and polls the stable collection resource, while AWS can later replace source acquisition and worker execution behind the same contract.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite durable commands/outbox/leases, trusted Git subprocesses, React 18, TypeScript, TanStack Query, Vitest, Testing Library, pytest.

---

### Task 1: Extract a reusable exact-revision collection application service

**Files:**
- Create: `apps/api/src/lineage_api/application/repository_collection.py`
- Modify: `apps/api/src/lineage_api/cli.py`
- Modify: `apps/api/src/lineage_api/dependencies.py`
- Test: `apps/api/tests/application/test_repository_collection.py`
- Test: `apps/api/tests/test_real_repository_cli.py`

**Step 1: Write failing application-service tests**

Add tests that construct a pinned `RepositorySnapshot`, invoke a `RepositoryCollectionService`, and require the existing summary contract:

```python
result = service.collect(descriptor)
assert result["outcome"] == "ACCEPTED"
assert result["commandId"]
assert result["runStatus"] == "IN_REVIEW"
assert result["counts"] == {
    "edges": 15,
    "reads": 10,
    "writes": 5,
    "residue": 0,
    "unresolved": 0,
}
```

Also require equivalent requests to return the same `commandId`, `runId`, and `proposalId` with a duplicate/reused outcome and no extra durable rows.

**Step 2: Run the tests and prove RED**

Run:

```bash
uv run --project apps/api --offline --frozen --no-sync pytest apps/api/tests/application/test_repository_collection.py apps/api/tests/test_real_repository_cli.py -q
```

Expected: failure because `RepositoryCollectionService` does not exist.

**Step 3: Implement the minimal reusable service**

Define bounded immutable input models for repository identity, analyzer selection, and an already-materialized snapshot. Move payload creation, deterministic event identity, HMAC signing, orchestration invocation, and result summarization out of `cli.py` into this service. Preserve the existing CLI JSON and exit-code contract by making the CLI a thin adapter.

The service result must include:

```python
{
    "collectionId": command_id,
    "commandId": command_id,
    "statusUrl": f"/api/collections/{command_id}",
    "outcome": outcome,
    "commandStatus": command_status,
    "runId": run_id,
    "runStatus": run_status,
    "proposalId": proposal_id,
    "proposalStatus": proposal_status,
    "runtimeStatus": runtime_status,
    "stages": stages,
    "coverageManifest": coverage_summary,
    "counts": counts,
}
```

Expose the collection service from `AppServices` without exposing concrete SQLite or filesystem classes to the API layer.

**Step 4: Run focused tests and prove GREEN**

Run the Step 2 command.

Expected: all focused tests pass and the CLI output remains backward-compatible.

**Step 5: Self-review and commit**

Check deterministic identity, duplicate side effects, bounded errors, and that `.gitignore` is not staged.

```bash
git add apps/api/src/lineage_api/application/repository_collection.py apps/api/src/lineage_api/cli.py apps/api/src/lineage_api/dependencies.py apps/api/tests/application/test_repository_collection.py apps/api/tests/test_real_repository_cli.py
git diff --cached --check
git commit -m "refactor: share exact repository collection"
```

### Task 2: Add source policy and safe Git URL acquisition

**Files:**
- Modify: `apps/api/src/lineage_api/config.py`
- Create: `apps/api/src/lineage_api/application/repository_acquisition.py`
- Create: `apps/api/src/lineage_api/infrastructure/remote_git_source.py`
- Modify: `apps/api/src/lineage_api/dependencies.py`
- Test: `apps/api/tests/application/test_repository_acquisition.py`
- Test: `apps/api/tests/infrastructure/test_remote_git_source.py`

**Step 1: Write failing source-policy tests**

Require:

- local paths are rejected unless `allow_local_repository_sources` is true;
- only HTTPS Git URLs are accepted by default;
- full 40-character hexadecimal revisions are required;
- URL, path, repository identity, analyzer fields, file count, file bytes, total bytes, timeout, and subprocess output are bounded;
- temporary directories are private and removed on success and failure;
- credential helpers, hooks, global/system Git configuration, inherited `PATH`, and ambient secrets are not passed to Git;
- a timeout terminates the complete process group;
- the checked-out `HEAD`, origin, and snapshot revision exactly match the request.

**Step 2: Run the tests and prove RED**

```bash
uv run --project apps/api --offline --frozen --no-sync pytest apps/api/tests/application/test_repository_acquisition.py apps/api/tests/infrastructure/test_remote_git_source.py -q
```

Expected: failure because the policy and remote adapter do not exist.

**Step 3: Implement minimal acquisition ports and adapters**

Add `RepositorySourceRequest` variants for `LOCAL_CHECKOUT` and `GIT`. Resolve local checkouts with `LocalGitRepositorySource`. For Git URL requests, create a private temporary directory, run trusted absolute Git with a minimal environment and explicit safe configuration, fetch only the exact commit, detach checkout, then call the same hardened local reader.

Use a bounded streaming supervisor for stdout/stderr and process-group timeout. Do not place URL credentials, local paths, or Git output in raised API-facing errors. Materialize the immutable in-memory snapshot before deleting the temporary checkout.

Add settings with safe defaults:

```python
allow_local_repository_sources: bool = False
repository_clone_timeout_seconds: int = 60
repository_git_output_limit_bytes: int = 64 * 1024
```

Development startup explicitly enables local paths; production remains disabled.

**Step 4: Run focused tests and prove GREEN**

Run the Step 2 command.

Expected: all acquisition and adversarial subprocess tests pass without network access by cloning from bounded local test remotes.

**Step 5: Self-review and commit**

```bash
git add apps/api/src/lineage_api/config.py apps/api/src/lineage_api/application/repository_acquisition.py apps/api/src/lineage_api/infrastructure/remote_git_source.py apps/api/src/lineage_api/dependencies.py apps/api/tests/application/test_repository_acquisition.py apps/api/tests/infrastructure/test_remote_git_source.py
git diff --cached --check
git commit -m "feat: acquire exact repository sources"
```

### Task 3: Expose durable collection submit and status APIs

**Files:**
- Modify: `apps/api/src/lineage_api/api_models.py`
- Modify: `apps/api/src/lineage_api/main.py`
- Modify: `apps/api/src/lineage_api/application/product_api.py`
- Modify: `apps/api/src/lineage_api/infrastructure/aws/product_api_entrypoint.py`
- Test: `apps/api/tests/test_api.py`
- Test: `apps/api/tests/application/test_product_api.py`
- Test: `apps/api/tests/entrypoints/aws/test_aws_product_api_entrypoint.py`

**Step 1: Write failing API contract tests**

Require `POST /api/collections` to accept both discriminated source variants and return `202`. Require `GET /api/collections/{commandId}` to return the same stable representation. Cover:

- forbidden extra fields and missing required fields;
- production rejection of `LOCAL_CHECKOUT`;
- malformed URL, non-immutable revision, incompatible analyzer/profile, and unknown command;
- exact duplicate submission returning the same identifiers;
- structured error code and correlation ID without local path, URL credentials, raw Git output, or exception text;
- parity through the environment-neutral `ProductApi` and AWS entry point.

**Step 2: Run the tests and prove RED**

```bash
uv run --project apps/api --offline --frozen --no-sync pytest apps/api/tests/test_api.py apps/api/tests/application/test_product_api.py apps/api/tests/entrypoints/aws/test_aws_product_api_entrypoint.py -q
```

Expected: `404` or model-import failures for the new routes.

**Step 3: Implement typed routes and status projection**

Add Pydantic discriminated request models with `extra="forbid"`, bounded strings, enum source kinds, exact revision validation, and cross-field analyzer/profile validation. Submit through the acquisition and collection application services. Always return `202` for accepted, duplicate, or reused submissions and set `Location` to the status URL.

Project command/run/proposal/coverage state through `GET /api/collections/{commandId}`. Add stable error mappings for source-policy, acquisition, analyzer, command-not-found, and terminal processing errors. Keep the application-neutral product route contract aligned for Lambda/API Gateway delivery.

**Step 4: Run focused tests and prove GREEN**

Run the Step 2 command.

Expected: focused API, product API, and AWS entry-point tests pass.

**Step 5: Self-review and commit**

```bash
git add apps/api/src/lineage_api/api_models.py apps/api/src/lineage_api/main.py apps/api/src/lineage_api/application/product_api.py apps/api/src/lineage_api/infrastructure/aws/product_api_entrypoint.py apps/api/tests/test_api.py apps/api/tests/application/test_product_api.py apps/api/tests/entrypoints/aws/test_aws_product_api_entrypoint.py
git diff --cached --check
git commit -m "feat: expose repository collection API"
```

### Task 4: Add the typed React collection client and form

**Files:**
- Modify: `apps/web/src/api/types.ts`
- Modify: `apps/web/src/api/client.ts`
- Modify: `apps/web/src/api/client.test.ts`
- Create: `apps/web/src/components/operations/RepositoryCollectionForm.tsx`
- Create: `apps/web/src/components/operations/RepositoryCollectionForm.test.tsx`
- Modify: `apps/web/src/pages/OperationsPage.tsx`
- Modify: `apps/web/src/styles.css`

**Step 1: Write failing client and form tests**

Require the client to serialize both source variants exactly and normalize the collection response defensively. Require the form to:

- default to local checkout only when demo/development actions are enabled;
- switch between checkout path and Git URL fields;
- require an exact revision and all repository/analyzer identity fields;
- submit once while pending;
- render accessible labels and errors;
- retain user-entered values after a server failure;
- keep the seeded demo as a secondary action.

**Step 2: Run tests and prove RED**

```bash
npm --workspace apps/web test -- --run src/api/client.test.ts src/components/operations/RepositoryCollectionForm.test.tsx
```

Expected: failures because collection request types, client methods, and form are absent.

**Step 3: Implement the minimal client and form**

Add `RepositoryCollectionRequest`, source variants, coverage/count summaries, and `RepositoryCollection` response types. Add:

```ts
submitCollection(body, signal?)
collection(commandId, signal?)
```

Implement the accessible source-mode form with explicit initial values for the Spring Data JPA analyzer pack, ruleset, and schema profile. Production rendering must omit local checkout mode rather than merely disabling its input.

Replace the placeholder launch card with the new primary collection panel. Preserve the seed/reset mutation as a labelled local demonstration below it.

**Step 4: Run focused tests and prove GREEN**

Run the Step 2 command.

Expected: client and form tests pass.

**Step 5: Self-review and commit**

```bash
git add apps/web/src/api/types.ts apps/web/src/api/client.ts apps/web/src/api/client.test.ts apps/web/src/components/operations/RepositoryCollectionForm.tsx apps/web/src/components/operations/RepositoryCollectionForm.test.tsx apps/web/src/pages/OperationsPage.tsx apps/web/src/styles.css
git diff --cached --check
git commit -m "feat: submit repository collections from UI"
```

### Task 5: Render polling progress and terminal collection results

**Files:**
- Create: `apps/web/src/components/operations/CollectionStatus.tsx`
- Create: `apps/web/src/components/operations/CollectionStatus.test.tsx`
- Modify: `apps/web/src/pages/OperationsPage.tsx`
- Modify: `apps/web/src/pages/workspaces.test.tsx`
- Modify: `apps/web/src/styles.css`

**Step 1: Write failing status and page tests**

Use deterministic fake timers to require polling only for non-terminal states. Verify:

- stage timeline and current command state;
- expected/completed/skipped/unsupported/failed coverage;
- edge/read/write/residue/unresolved counts;
- bounded API failure code and correlation ID;
- polling stops at terminal completion or failure;
- successful run and proposal links target existing routes;
- overview, runs, and proposals queries invalidate once after terminal completion.

**Step 2: Run tests and prove RED**

```bash
npm --workspace apps/web test -- --run src/components/operations/CollectionStatus.test.tsx src/pages/workspaces.test.tsx
```

Expected: missing component and behavior failures.

**Step 3: Implement polling and result rendering**

Use a TanStack Query keyed by command ID with a bounded polling interval only while the returned command state is non-terminal. Reuse `StageTimeline` for run stages. Render coverage and count values as semantic definition lists, errors as `role="alert"`, and state changes as a polite status region.

**Step 4: Run focused tests and prove GREEN**

Run the Step 2 command.

Expected: all status and workspace tests pass without timer leaks or duplicate requests.

**Step 5: Self-review and commit**

```bash
git add apps/web/src/components/operations/CollectionStatus.tsx apps/web/src/components/operations/CollectionStatus.test.tsx apps/web/src/pages/OperationsPage.tsx apps/web/src/pages/workspaces.test.tsx apps/web/src/styles.css
git diff --cached --check
git commit -m "feat: show repository collection progress"
```

### Task 6: Prove the integrated Petclinic product flow

**Files:**
- Create: `tests/integration/test_repository_collection_product_flow.py`
- Modify: `scripts/run_real_repository_acceptance.sh`
- Modify: `docs/acceptance/lineage-platform-acceptance.md`
- Modify: `README.md`
- Modify: `/tmp/refactor-lineagecollector.md`

**Step 1: Write the failing product-flow acceptance test**

Submit the pinned Spring Petclinic checkout through `POST /api/collections`, poll its status resource, then assert:

```python
assert status["runStatus"] == "IN_REVIEW"
assert status["proposalStatus"] == "IN_REVIEW"
assert status["counts"] == {
    "edges": 15,
    "reads": 10,
    "writes": 5,
    "residue": 0,
    "unresolved": 0,
}
assert status["coverageManifest"]["counts"] == {
    "expected": 131,
    "completed": 33,
    "skipped": 98,
    "unsupported": 0,
    "failed": 0,
}
```

Submit the same body again and assert identical durable IDs and unchanged database/evidence digests.

**Step 2: Run the opt-in test and prove RED**

```bash
LINEAGE_REAL_REPOSITORY_CHECKOUT=/tmp/lineage-spring-petclinic.8hgOwD/repo \
uv run --project apps/api --offline --frozen --no-sync pytest tests/integration/test_repository_collection_product_flow.py -q
```

Expected: failure until the new API is fully wired.

**Step 3: Complete only the integration wiring required by the test**

Update startup instructions so local API execution explicitly enables local repository sources. Document the UI fields, Git URL behavior, terminal result, and production prohibition on arbitrary local paths. Add the product-flow result to the retained acceptance summary without retaining source, paths, raw queries, credentials, or nondeterministic timestamps.

**Step 4: Run focused and full verification**

Run:

```bash
LINEAGE_REAL_REPOSITORY_CHECKOUT=/tmp/lineage-spring-petclinic.8hgOwD/repo \
uv run --project apps/api --offline --frozen --no-sync pytest tests/integration/test_repository_collection_product_flow.py -q
make verify
make workflow-check
make synth
```

Expected: Petclinic product flow passes; all backend, frontend, infrastructure, build, workflow, and 11-stack synthesis gates pass.

**Step 5: Run the application and browser smoke test**

Start the API with local repository sources explicitly enabled and the Vite app. Submit the pinned checkout through the visible form, confirm the terminal 15/10/5/0 result, open the run timeline, and open the proposal.

**Step 6: Independent review and final milestone commit**

Request separate specification and quality reviews of the exact integrated diff. Address every actionable finding with a new failing regression test. Re-run the affected focused tests and full verification before committing any fixes.

```bash
git add tests/integration/test_repository_collection_product_flow.py scripts/run_real_repository_acceptance.sh docs/acceptance/lineage-platform-acceptance.md README.md
git diff --cached --check
git commit -m "test: prove repository collection product flow"
```

Update `/tmp/refactor-lineagecollector.md` with exact commands, counts, checksums, review outcomes, commits, and remaining AWS/PR work.
