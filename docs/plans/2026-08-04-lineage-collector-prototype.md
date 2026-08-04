# Lineage Collector Local Prototype Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and verify a locally runnable end-to-end lineage collection walking skeleton from signed push intake through reviewed, fenced publication and lineage/impact exploration.

**Architecture:** A FastAPI modular monolith preserves the PRD component boundaries behind Python services and stores control state plus versioned projections in SQLite. A write-once filesystem adapter represents S3 truth, and a React/TypeScript SPA provides operations, review, run, and lineage workspaces. Shared JSON schemas, seeded catalog/repository fixtures, and test-first domain slices keep the local adapters replaceable by the AWS topology in L14.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, SQLite, pytest, React 19, TypeScript, Vite, Vitest, Testing Library, plain CSS, npm workspaces, Make.

---

## Delivery rules

- Follow red-green-refactor for every behavior below.
- Keep source materials unmodified and untracked unless intentionally added later.
- Commit only files belonging to the task named in each section.
- Keep generated runtime state under `data/`, which is gitignored.
- Every API error uses `{code, message, correlationId, details?}`.
- Every persisted JSON payload uses stable key ordering in checksum calculations.
- Use UTC ISO-8601 timestamps and omit unknown optional correlation fields.

### Task 1: Scaffold the monorepo and shared contract registry

**Files:**

- Create: `.gitignore`
- Create: `Makefile`
- Create: `package.json`
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/src/lineage_api/__init__.py`
- Create: `apps/api/src/lineage_api/contracts.py`
- Create: `apps/api/tests/test_contracts.py`
- Create: `packages/contracts/event-envelope.schema.json`
- Create: `packages/contracts/evidence-ref.schema.json`
- Create: `packages/contracts/consolidated-edge.schema.json`
- Create: `packages/contracts/proposal.schema.json`
- Create: `packages/contracts/accepted-manifest.schema.json`
- Create: `packages/contracts/impact-response.schema.json`

**Step 1: Write the failing contract-registry test**

Test that every expected schema exists, has `$schema`, `$id`, `title`, `type: object`, and disallows unknown fields where the PRDs require closed payloads. Test that `EventEnvelope` requires `eventId`, `eventType`, `correlationId`, `repo`, `digest`, `env`, and `system`.

```python
def test_contract_registry_loads_all_platform_contracts():
    registry = ContractRegistry(CONTRACTS_DIR)
    assert registry.names() == {
        "accepted-manifest", "consolidated-edge", "event-envelope",
        "evidence-ref", "impact-response", "proposal",
    }

def test_event_envelope_requires_correlation_contract():
    registry = ContractRegistry(CONTRACTS_DIR)
    errors = registry.validate("event-envelope", {"eventId": "delivery-1"})
    assert {error.path for error in errors} >= {
        "eventType", "correlationId", "repo", "digest", "env", "system"
    }
```

**Step 2: Run the test to verify it fails**

Run: `uv run --project apps/api pytest apps/api/tests/test_contracts.py -q`

Expected: FAIL because the package and registry do not exist.

**Step 3: Add the minimal project and registry implementation**

Use `jsonschema` for validation. `ContractRegistry` loads only `*.schema.json`, indexes by stem, returns sorted names, and converts validation failures into a small `ContractError(path, message)` dataclass.

Define strict JSON Schemas with version `1.0.0`, correlation fields, enums from the PRDs, and no empty-string defaults. Add root commands:

```makefile
setup:
	uv sync --project apps/api --extra dev
	npm install

test:
	uv run --project apps/api pytest
	npm test

dev:
	npm run dev
```

The root npm workspace owns `concurrently` and delegates the web workspace added later.

**Step 4: Run the test to verify it passes**

Run: `uv run --project apps/api pytest apps/api/tests/test_contracts.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add .gitignore Makefile package.json apps/api packages/contracts
git commit -m "feat: establish lineage contracts and project scaffold"
```

### Task 2: Add deterministic fixtures and SQLite control-state storage

**Files:**

- Create: `fixtures/catalog/catalog-snapshot-v1.json`
- Create: `fixtures/repositories/payments-pipeline/pipeline.py`
- Create: `fixtures/repositories/payments-pipeline/repository-evidence.json`
- Create: `fixtures/repositories/payments-pipeline/expected-lineage.json`
- Create: `apps/api/src/lineage_api/config.py`
- Create: `apps/api/src/lineage_api/db.py`
- Create: `apps/api/src/lineage_api/seed.py`
- Create: `apps/api/tests/test_db.py`
- Create: `apps/api/tests/test_seed.py`

**Step 1: Write failing storage and seed tests**

Test that the database enables foreign keys and WAL mode, creates all tables, and resets deterministically. The minimum tables are `events`, `runs`, `run_stages`, `classification_decisions`, `quarantines`, `edge_ledger`, `proposals`, `graph_versions`, `graph_edges`, `pointers`, `publish_reservations`, and `audit_events`.

Test that the catalog snapshot has a stable content digest and includes the exact raw/staging/analytics datasets and element names used by the repository fixture.

**Step 2: Verify red**

Run: `uv run --project apps/api pytest apps/api/tests/test_db.py apps/api/tests/test_seed.py -q`

Expected: FAIL because storage and fixtures do not exist.

**Step 3: Implement minimal storage**

Use `sqlite3` from the standard library with explicit transactions and row factories. `Database.initialize()` applies idempotent DDL. `reset_demo()` recreates generated state, loads the catalog fixture, and inserts graph version `v1` as an initial approved baseline with a pointer for `staging`.

Seed URNs include:

```text
urn:ldp:staging:snowflake:payments:raw.transactions#amount
urn:ldp:staging:snowflake:payments:raw.transactions#customer_id
urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue
urn:ldp:staging:snowflake:payments:analytics.daily_revenue#customer_id
```

The seeded source calls `read_dataset(...)` and `write_dataset(...)` with literal names and a transform map, plus one dynamic-name call that must become residue.

**Step 4: Verify green**

Run: `uv run --project apps/api pytest apps/api/tests/test_db.py apps/api/tests/test_seed.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add fixtures apps/api/src/lineage_api/config.py apps/api/src/lineage_api/db.py apps/api/src/lineage_api/seed.py apps/api/tests
git commit -m "feat: add deterministic demo state and persistence"
```

### Task 3: Implement catalog-backed URN resolution and quarantine

**Files:**

- Create: `apps/api/src/lineage_api/domain/urns.py`
- Create: `apps/api/src/lineage_api/services/resolver.py`
- Create: `apps/api/tests/domain/test_urns.py`
- Create: `apps/api/tests/services/test_resolver.py`

**Step 1: Write failing URN tests**

Cover grammar round-trip, environment identity, normalization order, registered aliases, zero matches, multiple matches, batch ordering, and one snapshot pin per batch. Require credentials in connection strings to be stripped before any quarantine persistence.

```python
def test_unknown_name_is_quarantined_and_never_guessed(resolver):
    result = resolver.resolve(RawName(kind="dataset", value="missing.table"), CONTEXT)
    assert result.status == "QUARANTINED"
    assert result.reason == "UNKNOWN_DATASET"
    assert result.candidates == []

def test_render_parse_round_trip():
    urn = LineageUrn.parse(
        "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
    )
    assert str(urn) == "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
```

**Step 2: Verify red**

Run: `uv run --project apps/api pytest apps/api/tests/domain/test_urns.py apps/api/tests/services/test_resolver.py -q`

Expected: FAIL because URN and resolver modules do not exist.

**Step 3: Implement minimal resolution**

Implement the grammar and the subset of normalization rules backed by fixture data: decode, case-fold non-authoritative dataset segments, default schema, configured alias, catalog match, and vocabulary validation. Record `rulesApplied`, `resolverVersion`, `snapshotId`, catalog reference, and candidates. Never synthesize a URN from an unmatched name.

**Step 4: Verify green and refactor**

Run: `uv run --project apps/api pytest apps/api/tests/domain/test_urns.py apps/api/tests/services/test_resolver.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/domain apps/api/src/lineage_api/services/resolver.py apps/api/tests
git commit -m "feat: add deterministic catalog-backed URN resolution"
```

### Task 4: Implement intake, deduplication, and repository classification

**Files:**

- Create: `apps/api/src/lineage_api/domain/errors.py`
- Create: `apps/api/src/lineage_api/services/intake.py`
- Create: `apps/api/src/lineage_api/services/classification.py`
- Create: `apps/api/tests/services/test_intake.py`
- Create: `apps/api/tests/services/test_classification.py`

**Step 1: Write failing intake and classification tests**

Test valid and invalid HMAC signatures, missing delivery ID/digest, dedupe by delivery ID, canonical envelope fields, and lane mapping. Test strict seven-level evidence precedence, `DATA_PIPELINE` selection, same-level conflict to `UNKNOWN`, and the rule that heuristics may never exclude.

```python
def test_duplicate_delivery_has_one_business_effect(intake, signed_push):
    first = intake.accept(signed_push)
    second = intake.accept(signed_push)
    assert first.outcome == "ACCEPTED"
    assert second.outcome == "DUPLICATE"
    assert first.event_id == second.event_id
    assert intake.run_count_for(first.event_id) == 0

def test_same_precedence_conflict_becomes_unknown(classifier):
    result = classifier.classify(CONFLICTING_MANIFEST_EVIDENCE)
    assert result.repository_class == "UNKNOWN"
    assert result.review_required is True
```

**Step 2: Verify red**

Run: `uv run --project apps/api pytest apps/api/tests/services/test_intake.py apps/api/tests/services/test_classification.py -q`

Expected: FAIL.

**Step 3: Implement minimal services**

Use a local demo secret from settings, compare signatures with `hmac.compare_digest`, and store only sanitized normalized fields. Use a database unique constraint for delivery IDs. Classification writes immutable decision history and returns a typed treatment used by orchestration.

**Step 4: Verify green**

Run: `uv run --project apps/api pytest apps/api/tests/services/test_intake.py apps/api/tests/services/test_classification.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/domain/errors.py apps/api/src/lineage_api/services apps/api/tests/services
git commit -m "feat: add durable intake and repository classification"
```

### Task 5: Implement deterministic SCA and write-once evidence

**Files:**

- Create: `apps/api/src/lineage_api/domain/evidence.py`
- Create: `apps/api/src/lineage_api/services/sca.py`
- Create: `apps/api/src/lineage_api/services/evidence_store.py`
- Create: `apps/api/tests/services/test_sca.py`
- Create: `apps/api/tests/services/test_evidence_store.py`

**Step 1: Write failing analysis and immutability tests**

Analyze the seeded file twice and require byte-identical evidence JSON. Exact assertions require file, line, AST path, raw source/target, transform, digest, ruleset version, and resolver pin. Dynamic names must produce residue with reason `dynamic-name`.

Test evidence put/get, checksum verification, same-content idempotency, different-content overwrite rejection, and schema version requirement.

**Step 2: Verify red**

Run: `uv run --project apps/api pytest apps/api/tests/services/test_sca.py apps/api/tests/services/test_evidence_store.py -q`

Expected: FAIL.

**Step 3: Implement minimal analysis and store**

Use Python `ast` to find literal `read_dataset` and `write_dataset` calls. Resolve all names through L01 before creating assertions. Serialize with sorted keys and compact separators. The filesystem adapter writes to `data/objects/{kind}/{key}.json` with exclusive creation, and indexes the stable `EvidenceRef` in SQLite.

**Step 4: Verify green**

Run: `uv run --project apps/api pytest apps/api/tests/services/test_sca.py apps/api/tests/services/test_evidence_store.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/domain/evidence.py apps/api/src/lineage_api/services apps/api/tests/services
git commit -m "feat: collect deterministic SCA evidence immutably"
```

### Task 6: Implement idempotent consolidation, confidence, and conflicts

**Files:**

- Create: `apps/api/src/lineage_api/domain/confidence.py`
- Create: `apps/api/src/lineage_api/services/consolidation.py`
- Create: `apps/api/tests/domain/test_confidence.py`
- Create: `apps/api/tests/services/test_consolidation.py`

**Step 1: Write the failing band-matrix and merge tests**

Table-test all distinct mechanism sets. Verify LLM-only remains LOWEST, dataset corroboration never changes a band, replay adds no duplicate provenance, engine arrival order produces identical JSON, and differing exact transforms yield `CONFLICTING` with both records intact.

```python
@pytest.mark.parametrize(("mechanisms", "band"), [
    ({"LLM"}, "LOWEST"),
    ({"SCA"}, "SINGLE"),
    ({"RUNTIME"}, "SINGLE"),
    ({"SCA", "LLM"}, "MEDIUM"),
    ({"RUNTIME", "LLM"}, "MEDIUM"),
    ({"SCA", "RUNTIME"}, "HIGH"),
    ({"SCA", "RUNTIME", "LLM"}, "HIGHEST"),
])
def test_ordinal_band_matrix(mechanisms, band):
    assert derive_band(mechanisms) == band
```

**Step 2: Verify red**

Run: `uv run --project apps/api pytest apps/api/tests/domain/test_confidence.py apps/api/tests/services/test_consolidation.py -q`

Expected: FAIL.

**Step 3: Implement minimal merge behavior**

Use a stable edge key, provenance IDs, sorted provenance arrays, and optimistic SQLite updates. Keep `band`, `corroboration`, `status`, normalized transform, and evidence refs. Add fixture-adapter methods for validated LLM and runtime assertions so their contract behavior can be demonstrated.

**Step 4: Verify green**

Run: `uv run --project apps/api pytest apps/api/tests/domain/test_confidence.py apps/api/tests/services/test_consolidation.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/domain/confidence.py apps/api/src/lineage_api/services/consolidation.py apps/api/tests
git commit -m "feat: consolidate evidence with ordinal confidence"
```

### Task 7: Implement proposal review and immutable correction history

**Files:**

- Create: `apps/api/src/lineage_api/domain/proposals.py`
- Create: `apps/api/src/lineage_api/services/review.py`
- Create: `apps/api/tests/domain/test_proposals.py`
- Create: `apps/api/tests/services/test_review.py`

**Step 1: Write failing lifecycle tests**

Require legal transitions only, actor/correlation audit records, conditional decision versions, stale-base detection, rejection history retention, and corrections that create linked successor edge/proposal versions.

```python
def test_approved_proposal_cannot_be_rejected(review, pending_proposal):
    review.approve(pending_proposal.id, actor="reviewer@example.test")
    with pytest.raises(DomainError, match="INVALID_PROPOSAL_TRANSITION"):
        review.reject(pending_proposal.id, actor="reviewer@example.test")
```

**Step 2: Verify red**

Run: `uv run --project apps/api pytest apps/api/tests/domain/test_proposals.py apps/api/tests/services/test_review.py -q`

Expected: FAIL.

**Step 3: Implement the server-owned lifecycle**

Create proposals deterministically from consolidated edge diffs against an expected graph version. Preserve before/after payloads and evidence refs. For the demo, parser-exact edges stay in `IN_REVIEW`; stamp their real `autoPublishable` field but explain the prototype override.

**Step 4: Verify green**

Run: `uv run --project apps/api pytest apps/api/tests/domain/test_proposals.py apps/api/tests/services/test_review.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/domain/proposals.py apps/api/src/lineage_api/services/review.py apps/api/tests
git commit -m "feat: add auditable lineage proposal review"
```

### Task 8: Implement fenced publication, active projections, and impact analysis

**Files:**

- Create: `apps/api/src/lineage_api/domain/impact.py`
- Create: `apps/api/src/lineage_api/services/publisher.py`
- Create: `apps/api/src/lineage_api/services/query.py`
- Create: `apps/api/tests/services/test_publisher.py`
- Create: `apps/api/tests/services/test_query.py`
- Create: `apps/api/tests/domain/test_impact.py`

**Step 1: Write failing fencing and query tests**

Test monotonic tokens, stale-worker rejection, expected-prior mismatch, stage verification before pointer swap, rollback as a new pointer event, version-pinned reads, traversal depth at most five, and the full L11 section 15 severity matrix.

```python
def test_expired_publisher_cannot_advance_pointer(publisher):
    stale = publisher.reserve(env="staging", expected_prior="v1")
    current = publisher.reserve(env="staging", expected_prior="v1")
    with pytest.raises(DomainError, match="FENCE_LOST"):
        publisher.activate(stale, manifest_ref="manifest-a")
    assert publisher.pointer("staging") == "v1"

def test_column_drop_on_high_edge_blocks():
    assert severity_for("COLUMN_DROP", "HIGH") == "BLOCK"
```

**Step 2: Verify red**

Run: `uv run --project apps/api pytest apps/api/tests/services/test_publisher.py apps/api/tests/services/test_query.py apps/api/tests/domain/test_impact.py -q`

Expected: FAIL.

**Step 3: Implement publication and queries**

Write accepted manifests through the evidence store. Reserve with a monotonic integer token, stage a complete inactive namespace, compare count/checksum, and update the pointer within one transaction only when token and prior version match. Query only through the pointer unless an explicit retained version is supplied.

Implement breadth-first downstream traversal with path/edge provenance, deterministic sorting, `truncated`, summaries, and severity mapping from L11.

**Step 4: Verify green**

Run: `uv run --project apps/api pytest apps/api/tests/services/test_publisher.py apps/api/tests/services/test_query.py apps/api/tests/domain/test_impact.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/domain/impact.py apps/api/src/lineage_api/services apps/api/tests
git commit -m "feat: publish and query fenced lineage projections"
```

### Task 9: Wire orchestration and the FastAPI surface

**Files:**

- Create: `apps/api/src/lineage_api/services/orchestration.py`
- Create: `apps/api/src/lineage_api/api_models.py`
- Create: `apps/api/src/lineage_api/dependencies.py`
- Create: `apps/api/src/lineage_api/main.py`
- Create: `apps/api/tests/services/test_orchestration.py`
- Create: `apps/api/tests/test_api.py`
- Create: `tests/test_walking_skeleton.py`

**Step 1: Write failing orchestration and API integration tests**

Drive reset → signed push → proposal detail → approve → active pointer changes → lineage query → impact query. Assert one run, ordered stage history, correlation propagation, evidence refs, proposal state, immutable approval, namespace version, and a BLOCK result for a high-confidence destructive change.

Test duplicate push, invalid signature, classification UNKNOWN, depth exceeded, missing resource, and concurrent proposal decision error shapes.

**Step 2: Verify red**

Run: `uv run --project apps/api pytest apps/api/tests/services/test_orchestration.py apps/api/tests/test_api.py tests/test_walking_skeleton.py -q`

Expected: FAIL.

**Step 3: Implement the workflow and routes**

The synchronous local workflow records these stages:

```text
QUEUED → CLASSIFYING → ANALYZING → RESOLVING → STORING_EVIDENCE
→ MERGING → PROPOSING → IN_REVIEW
```

Approval continues `PUBLISHING → PUBLISHED`. Failures write `FAILED` with `failedStage` and safe detail. Expose every endpoint listed in the design document plus `/healthz`. Add CORS for the Vite dev origin only.

**Step 4: Verify green**

Run: `uv run --project apps/api pytest apps/api/tests/services/test_orchestration.py apps/api/tests/test_api.py tests/test_walking_skeleton.py -q`

Expected: PASS.

**Step 5: Run the complete backend suite**

Run: `uv run --project apps/api pytest -q`

Expected: all backend tests PASS with no warnings.

**Step 6: Commit**

```bash
git add apps/api/src/lineage_api apps/api/tests tests
git commit -m "feat: expose the end-to-end collection workflow"
```

### Task 10: Build the frontend shell and typed API client

**Files:**

- Create: `apps/web/package.json`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/index.html`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/App.tsx`
- Create: `apps/web/src/api/client.ts`
- Create: `apps/web/src/api/types.ts`
- Create: `apps/web/src/styles/tokens.css`
- Create: `apps/web/src/styles/global.css`
- Create: `apps/web/src/components/layout/AppShell.tsx`
- Create: `apps/web/src/components/shared/StatusPill.tsx`
- Create: `apps/web/src/test/setup.ts`
- Create: `apps/web/src/App.test.tsx`

**Step 1: Write a failing shell test**

Render the app and require accessible primary navigation for Operations, Review Queue, Lineage Explorer, and Runs; an active namespace watermark; skip link; and no color-only status label.

**Step 2: Verify red**

Run: `npm test --workspace apps/web -- --run src/App.test.tsx`

Expected: FAIL because the web workspace does not exist.

**Step 3: Implement the shell and design system**

Use React Router, TanStack Query, and CSS custom properties. Apply the approved warm-paper/ink/teal/rust direction with editorial typography, compact evidence metadata, responsive navigation, visible focus, and reduced-motion handling. Do not use a component framework.

The typed client handles the common error shape, loading, abort signals, and empty states. Vite proxies `/api` to `http://127.0.0.1:8000`.

**Step 4: Verify green**

Run: `npm test --workspace apps/web -- --run src/App.test.tsx`

Expected: PASS.

**Step 5: Commit**

```bash
git add package.json package-lock.json apps/web
git commit -m "feat: establish the lineage operations interface"
```

### Task 11: Build Operations, Run Timeline, and Review workspaces

**Files:**

- Create: `apps/web/src/pages/OperationsPage.tsx`
- Create: `apps/web/src/pages/RunsPage.tsx`
- Create: `apps/web/src/pages/RunDetailPage.tsx`
- Create: `apps/web/src/pages/ReviewQueuePage.tsx`
- Create: `apps/web/src/pages/ProposalDetailPage.tsx`
- Create: `apps/web/src/components/operations/GateCard.tsx`
- Create: `apps/web/src/components/operations/FlowRail.tsx`
- Create: `apps/web/src/components/runs/StageTimeline.tsx`
- Create: `apps/web/src/components/review/EdgeDiff.tsx`
- Create: `apps/web/src/components/review/ProvenancePanel.tsx`
- Create: `apps/web/src/pages/workspaces.test.tsx`

**Step 1: Write failing user-flow component tests**

With mocked HTTP responses, test overview gate values and collection CTA, stage labels and correlation links, review queue routing, proposal evidence/provenance display, approval confirmation, rejection rationale, and server-error recovery.

**Step 2: Verify red**

Run: `npm test --workspace apps/web -- --run src/pages/workspaces.test.tsx`

Expected: FAIL.

**Step 3: Implement the three workspaces**

Operations leads with the active version and push-to-publish rail, not generic charts. The CTA resets/runs the seeded collection and links to its timeline. Proposal detail shows before/after, band and corroboration separately, mechanism badges, file/line evidence, checksum references, and the human-gate actions.

Use query invalidation after mutations so state always reflects the server.

**Step 4: Verify green**

Run: `npm test --workspace apps/web -- --run src/pages/workspaces.test.tsx`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/src
git commit -m "feat: add operations timeline and review workflows"
```

### Task 12: Build the lineage explorer and impact simulator

**Files:**

- Create: `apps/web/src/pages/LineageExplorerPage.tsx`
- Create: `apps/web/src/components/lineage/LineageCanvas.tsx`
- Create: `apps/web/src/components/lineage/EdgeInspector.tsx`
- Create: `apps/web/src/components/lineage/ImpactPanel.tsx`
- Create: `apps/web/src/components/lineage/lineageLayout.ts`
- Create: `apps/web/src/components/lineage/lineageLayout.test.ts`
- Create: `apps/web/src/pages/LineageExplorerPage.test.tsx`

**Step 1: Write failing layout and interaction tests**

Require stable node positions, directional edges, keyboard-selectable nodes/edges, upstream/downstream and depth controls, active version watermark, edge provenance inspector, all six impact change types, and BLOCK/WARN/INFO summaries.

**Step 2: Verify red**

Run: `npm test --workspace apps/web -- --run src/components/lineage/lineageLayout.test.ts src/pages/LineageExplorerPage.test.tsx`

Expected: FAIL.

**Step 3: Implement the explorer**

Render a focused SVG graph without a heavy graph dependency. Use deterministic layered layout, labeled nodes, arrow markers, selected path emphasis, accessible fallback lists, and restrained entry motion. Never render an edge without its band and mechanism summary.

The impact panel submits the exact L11 change enum and displays affected consumers, path length, band, corroboration, evidence link, and maximum verdict.

**Step 4: Verify green**

Run: `npm test --workspace apps/web -- --run src/components/lineage/lineageLayout.test.ts src/pages/LineageExplorerPage.test.tsx`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/src
git commit -m "feat: add lineage and impact exploration"
```

### Task 13: Document PRD ambiguities and local operation

**Files:**

- Create: `README.md`
- Create: `docs/prd-ambiguities.md`
- Create: `docs/prototype-coverage.md`
- Modify: `Makefile`
- Modify: `package.json`

**Step 1: Write a failing documentation check**

Create `tests/test_documentation.py` that requires:

- setup, dev, reset, test, and build commands in README;
- every L01–L16 represented in the coverage matrix;
- ambiguity entries with ID, classification, source, impact, prototype decision, and production owner;
- all CTX-01 through CTX-17 present;
- cross-document inconsistencies called out separately.

**Step 2: Verify red**

Run: `uv run --project apps/api pytest tests/test_documentation.py -q`

Expected: FAIL because the documents do not exist.

**Step 3: Write the documentation and ambiguity register**

At minimum, record:

- CTX-01 through CTX-17 as configuration seams, never invented values.
- G-L01-1 platform normalization/case/quote tables.
- G-L03-3 stage-level ASL input/output specifications.
- G-L04-1 complete rule-pack matcher tables.
- G-L05-1 prompt templates and open budget caps.
- G-L06-1 OTel attribute mapping table.
- G-L09-2 calibration corpus JSON Schema.
- L15 proposed availability targets and projection rebuild RTO.
- L02 dedupe TTL conflict: PRD says 14 days while architecture slide 16 says 7 days.
- L09 MVP auto-publish versus L16 M1 manual-review demonstration.
- L16 phase wording that places Baseline in M2 while L03 labels Baseline MVP.
- Open namespace/evidence retention values and ownership mapping.
- Unspecified review SPA information architecture and async impact notification details.

For each, state the reversible prototype assumption. Include the exact local start URL and demo walkthrough.

**Step 4: Verify green**

Run: `uv run --project apps/api pytest tests/test_documentation.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add README.md docs Makefile package.json tests/test_documentation.py
git commit -m "docs: add local guide coverage and PRD ambiguity register"
```

### Task 14: Add browser coverage and complete verification

**Files:**

- Create: `apps/web/e2e/walking-skeleton.spec.ts`
- Create: `apps/web/playwright.config.ts`
- Modify: `apps/web/package.json`
- Modify: `package.json`

**Step 1: Write the browser test before relying on manual behavior**

The test must:

1. reset the demo;
2. start a seeded push collection;
3. open the resulting run and verify its stage timeline;
4. open the proposal and inspect SCA provenance;
5. approve it and observe a new active namespace;
6. open the lineage explorer and select an edge;
7. run `COLUMN_DROP` impact and observe a band-gated verdict;
8. verify the duplicate event path creates no second run.

**Step 2: Run it and verify red**

Run: `npm run test:e2e`

Expected: FAIL until the app/test wiring and production-like startup are complete.

**Step 3: Add production-like local serving and finish E2E wiring**

Configure Playwright web servers for the FastAPI app and Vite preview. Add root scripts `dev`, `build`, `test`, `test:e2e`, and `verify`. Ensure API and web ports are documented and configurable.

**Step 4: Verify the browser flow**

Run: `npm run test:e2e`

Expected: PASS.

**Step 5: Run the complete verification gate**

Run: `make test`

Expected: backend and frontend unit/integration tests PASS.

Run: `npm run build`

Expected: TypeScript and Vite production build succeed with exit 0.

Run: `npm run test:e2e`

Expected: walking-skeleton browser test PASS.

Run: `git diff --check`

Expected: exit 0 with no whitespace errors.

Run: `git status --short`

Expected: only intentionally untracked source materials or ignored generated state; no unfinished implementation changes.

**Step 6: Commit**

```bash
git add apps/web package.json package-lock.json
git commit -m "test: verify the local lineage walking skeleton"
```

## Completion audit

Before declaring the goal complete, re-read the approved design and verify each of these with current evidence:

- A new user can follow README and run the prototype locally.
- The signed push-to-query workflow succeeds end to end.
- All named platform invariants represented by the prototype have a passing test.
- The UI exposes operations, review, run timeline, lineage, provenance, and impact behavior.
- The ambiguity register cites all known open CTX seams, authoring gaps, inconsistencies, and prototype assumptions.
- Backend tests, frontend tests, production build, browser test, and a live health request all pass freshly.
- No source material was modified and no generated state was committed.
