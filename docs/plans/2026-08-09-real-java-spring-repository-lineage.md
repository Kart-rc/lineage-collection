# Real Java Spring Repository Lineage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Analyze an exact external Spring Boot checkout safely, produce deterministic evidence-backed
Spring Data/JPA read/write lineage, and carry the result to the existing review and publication flow.

**Architecture:** Separate source acquisition from analysis with a bounded `RepositorySource` port.
Select analyzers through a closed registry: the existing Python pack remains the demo oracle and a
new source-only Tree-sitter/SQLGlot Java Spring pack handles the approved framework cell. The real
repository CLI verifies the Git determinant pins, submits the normal durable push command, and stops
at `IN_REVIEW` unless an existing explicit review action publishes it.

**Tech Stack:** Python 3.12/3.13, FastAPI application core, SQLite local ports, Tree-sitter Java,
SQLGlot, pytest, JSON Schema, Git CLI, existing evidence/consolidation/review/publication services.

---

### Task J1: Add the exact-checkout source contract and bounded local Git adapter

**Files:**

- Create: `packages/contracts/repository-checkout.schema.json`
- Create: `apps/api/src/lineage_api/application/repository_sources.py`
- Create: `apps/api/src/lineage_api/infrastructure/local_git_source.py`
- Create: `apps/api/tests/infrastructure/test_local_git_source.py`
- Modify: `apps/api/tests/test_contracts.py`

**Step 1: Write failing contract and adapter tests**

Specify a closed descriptor with canonical HTTPS origin, repository, exact 40/64-hex revision,
checkout root, environment/platform/system, analyzer pack and ruleset. Test a temporary Git checkout
for exact revision/origin, sorted tracked paths and deterministic scope digest. Add fail-closed cases
for wrong revision, index/path/type/mode drift, credential-bearing origin, symlink, submodule, path
escape, file-count, per-file and total-byte bounds. Verify that regular worktree transformations or
dirty bytes never replace the exact committed blob bytes supplied to analyzers.

```python
snapshot = LocalGitRepositorySource(limits).snapshot(descriptor)
assert snapshot.revision == descriptor.revision
assert snapshot.paths == tuple(sorted(snapshot.paths))
assert snapshot.scope_digest.startswith("sha256:")
```

**Step 2: Verify RED**

Run:

```bash
uv run --project apps/api --extra dev pytest -q \
  apps/api/tests/infrastructure/test_local_git_source.py \
  apps/api/tests/test_contracts.py
```

Expected: missing contract/module failures.

**Step 3: Implement the source port and adapter**

Use the bounded binary process runner with fixed Git argv, `shell=False`, a sanitized environment and
explicit stdout/stderr/time limits. Strip URL userinfo before comparing/reporting origins. Enumerate
`git ls-files -z`, reject non-regular or escaping resolved paths, bind the index to the exact HEAD
tree, and read each verified blob OID through bounded trusted Git plumbing without filters. Hash
canonical path/size/committed-blob tuples and return immutable `RepositorySnapshot` metadata plus a
bounded `read_bytes(relative_path)` method. Never invoke a repository-controlled executable.

**Step 4: Verify GREEN and regressions**

Run the focused command, then:

```bash
uv run --project apps/api --extra dev pytest -q
git diff --check
```

**Step 5: Self-review and commit**

Review command injection, credential redaction, TOCTOU, symlink/submodule behavior and deterministic
scope identity. Journal RED/GREEN/review evidence, stage only J1 files and commit:

```bash
git commit -m "feat: verify bounded repository checkouts"
```

### Task J2: Build the closed Java/Spring framework classifier and syntax-fact pack

**Files:**

- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`
- Create: `apps/api/src/lineage_api/services/java_spring_sca.py`
- Create: `apps/api/tests/services/test_java_spring_sca.py`
- Create: `fixtures/repositories/java-spring-corpus/pom.xml`
- Create: `fixtures/repositories/java-spring-corpus/build.gradle`
- Create: `fixtures/repositories/java-spring-corpus/src/main/java/example/Owner.java`
- Create: `fixtures/repositories/java-spring-corpus/src/main/java/example/OwnerRepository.java`
- Create: `fixtures/repositories/java-spring-corpus/src/main/java/example/OwnerController.java`
- Create: `fixtures/repositories/java-spring-corpus/src/main/resources/db/h2/schema.sql`

**Step 1: Add pinned parser dependencies and failing fact tests**

Pin compatible `tree-sitter`, `tree-sitter-java` and `sqlglot` releases in the lockfile. Tests must
prove Spring Boot/Data JPA classification from build files, entity/table facts, repository/entity
generic binding, constructor/field repository binding, method invocation locations and dialect-bound
schema tables. Include comments/strings, nested syntax, malformed Java, unknown framework and an
unsupported dynamic `@Query`.

```python
facts = JavaSpringAnalyzerPack().inspect(snapshot, profile="h2")
assert facts.framework_cell == "SPRING_BOOT_DATA_JPA"
assert facts.entities["Owner"].table == "owners"
assert facts.repositories["OwnerRepository"].entity == "Owner"
```

**Step 2: Verify RED**

Run:

```bash
uv run --project apps/api --extra dev pytest -q \
  apps/api/tests/services/test_java_spring_sca.py
```

Expected: missing analyzer/dependency failures.

**Step 3: Implement minimal syntax facts**

Use Tree-sitter nodes and fields for declarations, annotations, types, methods and invocations; never
use regex to interpret Java structure. XML parsing may read Maven coordinates; Gradle support is
closed to literal plugin/dependency declarations and otherwise reports residue. Parse the selected
schema with SQLGlot’s explicit dialect. Store exact path, line, byte range and stable AST path with
each fact.

**Step 4: Verify GREEN and regressions**

Run focused tests, the backend suite and `git diff --check`.

**Step 5: Self-review and commit**

Review parser-error accounting, supported-version closure, file/AST bounds and lack of code
execution. Journal and commit:

```bash
git commit -m "feat: extract bounded java spring facts"
```

### Task J3: Compile conservative JPA read/write evidence with complete coverage

**Files:**

- Modify: `apps/api/src/lineage_api/services/java_spring_sca.py`
- Modify: `apps/api/src/lineage_api/domain/evidence.py`
- Modify: `apps/api/tests/services/test_java_spring_sca.py`
- Create: `apps/api/tests/services/test_java_spring_determinism.py`

**Step 1: Write failing lineage, residue and determinism tests**

Assert exact call-site mappings:

```python
assert edge.lineage_tuple == (
    "urn:ldp:staging:h2:petclinic:owners",
    "service://spring-petclinic/example.OwnerController#findOwners",
    "READS",
    "OwnerRepository.findByLastName -> owners",
)
```

Add save/delete write cases, literal `@Query` cases, missing/ambiguous table, unresolved repository,
unknown method, dynamic query, malformed file and shuffled path order. Zero supported Java files or
incomplete scope must return `INTEGRATION_REQUIRED`, not successful empty evidence. Replays must be
byte-identical.

**Step 2: Verify RED**

Run both Java/Spring test modules. Expected: missing edge compilation and coverage fields.

**Step 3: Implement conservative evidence compilation**

Map only cited operations. Normalize tables to `LineageUrn(environment, profile, system, table)` and
service methods to stable `service://` identities. Extend SCA evidence only as needed to carry
coverage/unsupported status without changing existing Python serialization. Provenance identity
includes origin, revision, scope digest, pack/ruleset, schema profile and exact AST citation.

**Step 4: Verify GREEN and regressions**

Run focused tests, full backend tests, contract tests and `git diff --check`.

**Step 5: Self-review and commit**

Review semantic inflation, exact-vs-executed language, operation naming, table/profile ambiguity,
duplicate collapse and deterministic ordering. Journal and commit:

```bash
git commit -m "feat: compile spring data access lineage"
```

### Task J4: Route exact external checkouts through durable collection and review

**Files:**

- Create: `apps/api/src/lineage_api/services/analyzer_registry.py`
- Modify: `apps/api/src/lineage_api/services/sca.py`
- Modify: `apps/api/src/lineage_api/services/orchestration.py`
- Modify: `apps/api/src/lineage_api/dependencies.py`
- Modify: `apps/api/src/lineage_api/cli.py`
- Modify: `apps/api/tests/services/test_orchestration.py`
- Create: `apps/api/tests/test_real_repository_cli.py`
- Modify: `README.md`

**Step 1: Write failing closed-registry and end-to-review tests**

Test that Python and Java packs are explicit, unknown/mismatched packs fail before analysis, and the
orchestrator gets source paths through the port instead of `fixture_root/repositories/<repo>`.
Exercise `collect-checkout --checkout ... --origin ... --revision ... --profile h2` and assert the
normal signed intake/durable command/stage ledger/evidence/consolidation/proposal path ends in
`IN_REVIEW`. Assert runtime status is `NOT_PROVIDED`; never manufacture runtime evidence.

**Step 2: Verify RED**

Run:

```bash
uv run --project apps/api --extra dev pytest -q \
  apps/api/tests/test_real_repository_cli.py \
  apps/api/tests/services/test_orchestration.py
```

Expected: source/registry/CLI boundary does not exist.

**Step 3: Implement the registry and CLI composition**

Introduce an analyzer protocol that does not expose private resolver fields. Keep fixture services as
the default `make dev` composition. The real-repository CLI composes `LocalGitRepositorySource`, the
closed analyzer registry and existing SQLite/evidence/review services, creates a canonical signed
push delivery bound to the exact revision and tracked scope, processes the durable command, prints a
bounded JSON summary and stops before approval.

**Step 4: Verify GREEN and all local gates**

Run focused tests, `make verify`, `make workflow-check`, `make acceptance-smoke` and
`git diff --check`.

**Step 5: Self-review and commit**

Review source trust boundaries, demo regression, runtime truthfulness, duplicate delivery behavior,
review/publication authority and CLI secret output. Journal and commit:

```bash
git commit -m "feat: collect lineage from exact checkouts"
```

### Task J5: Prove the pinned Spring Petclinic compatibility cell

**Files:**

- Create: `tests/integration/test_spring_petclinic_repository.py`
- Create: `scripts/run_real_repository_acceptance.sh`
- Modify: `scripts/run_acceptance.sh`
- Modify: `docs/acceptance/lineage-platform-acceptance.md`
- Modify: `docs/prototype-coverage.md`
- Modify: `README.md`

**Step 1: Write the opt-in failing acceptance test**

Require `LINEAGE_REAL_REPOSITORY_CHECKOUT` and exact revision
`88e37c15cf6fc8490b01bc3e8e2c800cec1ac272`. When absent, emit/record
`INTEGRATION_REQUIRED`; do not pass. When present, assert the pinned origin, nonzero analyzed Java
files, the expected Petclinic repository/entity/table set, nonzero `READS` and `WRITES`, zero silent
scope loss, stable evidence checksum, `IN_REVIEW`, and duplicate no-effect.

**Step 2: Verify RED on the real clone**

Run:

```bash
LINEAGE_REAL_REPOSITORY_CHECKOUT=/tmp/lineage-spring-petclinic.8hgOwD/repo \
  ./scripts/run_real_repository_acceptance.sh
```

Expected: the new acceptance requirements fail before integration is complete.

**Step 3: Implement the evidence runner and documentation**

Write a content-addressed manifest under `data/acceptance/<run>/java-spring/` containing source URL,
revision, scope/analyzer/ruleset/schema/catalog/evidence digests, counts, residue and outcome. Do not
copy external source. Add exact operator commands and distinguish `LOCAL_REAL_REPOSITORY_PASS` from
hermetic fixture tests and live AWS evidence.

**Step 4: Verify the real cell and full regression gates**

Run the real acceptance command twice and compare evidence checksums, then:

```bash
make verify
make workflow-check
make acceptance-smoke
make synth
git diff --check
```

Expected: all local gates pass; AWS/runtime external rows remain truthful.

**Step 5: Final self-review and commit**

Review the design line by line, verify every real-repository claim has retained evidence, confirm the
external source is not vendored, update `/tmp/refactor-lineagecollector.md`, stage only J5 files and
commit:

```bash
git commit -m "test: prove lineage on spring petclinic"
```
