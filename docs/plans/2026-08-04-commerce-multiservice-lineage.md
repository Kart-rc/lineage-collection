# Commerce Multi-Service Lineage Simulation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Docker-free three-stage commerce simulation that collects static and runtime lineage from API, PostgreSQL, Kafka, S3, and Spark interactions, then reviews, publishes, and queries the complete source-to-gold graph.

**Architecture:** Extend the catalog and static analyzer to understand typed multi-platform assets and multi-source mappings. Add pure deterministic commerce adapters and a scenario orchestration service that persists a correlated event ledger, stores immutable SCA/OTel/Kafka/S3/OpenLineage evidence, consolidates assertions, and creates one proposal. Add a Scenario Lab page that replays the returned event ledger and links into the existing review, publication, lineage, and impact flows.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, SQLite, JSON Schema, pytest, React 18, TypeScript, TanStack Query, Vitest, Testing Library, Vite, local Chromium verification.

**Design reference:** `docs/plans/2026-08-04-commerce-multiservice-lineage-design.md`

**Execution discipline:** Use `@test-driven-development` for every behavior change, `@frontend-design` for Scenario Lab implementation, `@verification-before-completion` before handoff, and `@playwright` for the final browser walkthrough. Preserve the current payments-pipeline demo and all existing tests.

---

### Task 1: Extend strict contracts for scenario runs and interaction edges

**Files:**
- Create: `packages/contracts/scenario-run.schema.json`
- Modify: `packages/contracts/consolidated-edge.schema.json`
- Modify: `apps/api/tests/test_contracts.py`

**Step 1: Write the failing contract tests**

Add a representative strict scenario payload and validate the new edge types:

```python
def test_scenario_run_contract_is_strict() -> None:
    schema = load_schema("scenario-run.schema.json")
    payload = {
        "schemaVersion": "1.0.0",
        "scenarioId": "scenario-commerce-001",
        "outcome": "ACCEPTED",
        "state": "IN_REVIEW",
        "correlationId": "corr-commerce-001",
        "traceId": "trace-commerce-001",
        "runId": "run-commerce-001",
        "events": [],
        "artifacts": [],
        "evidence": [],
        "proposal": None,
    }
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate({**payload, "unexpected": True})


@pytest.mark.parametrize(
    "edge_type", ["CALLS", "READS", "WRITES", "PRODUCES", "CONSUMES", "DERIVES"]
)
def test_consolidated_edge_accepts_scenario_relationships(edge_type: str) -> None:
    payload = consolidated_edge_payload(edgeType=edge_type)
    Draft202012Validator(load_schema("consolidated-edge.schema.json")).validate(payload)
```

**Step 2: Run the tests to verify they fail**

Run:

```bash
uv run --project apps/api --extra dev pytest apps/api/tests/test_contracts.py -q
```

Expected: FAIL because `scenario-run.schema.json` is missing and new edge types are rejected.

**Step 3: Add the strict schema and enums**

The scenario schema must use `additionalProperties: false` at the top level and require:

```json
{
  "schemaVersion": "1.0.0",
  "scenarioId": "scenario-commerce-001",
  "outcome": "ACCEPTED | DUPLICATE | FAILED",
  "state": "QUEUED | RUNNING | IN_REVIEW | PUBLISHED | FAILED",
  "correlationId": "corr-commerce-001",
  "traceId": "trace-commerce-001",
  "runId": "run-commerce-001",
  "events": [],
  "artifacts": [],
  "evidence": [],
  "proposal": null
}
```

Permit `CALLS`, `PRODUCES`, and `CONSUMES` in addition to existing consolidated edge types.

**Step 4: Run the contract tests**

Expected: PASS.

**Step 5: Commit**

```bash
git add packages/contracts apps/api/tests/test_contracts.py
git commit -m "feat: define commerce scenario contracts"
```

---

### Task 2: Expand catalog identity and expose asset kinds

**Files:**
- Modify: `fixtures/catalog/catalog-snapshot-v1.json`
- Modify: `apps/api/src/lineage_api/services/resolver.py`
- Modify: `apps/api/src/lineage_api/services/query.py`
- Modify: `apps/api/src/lineage_api/dependencies.py`
- Modify: `apps/api/tests/services/test_resolver.py`
- Modify: `apps/api/tests/services/test_query.py`

**Step 1: Write failing resolver tests**

Cover each platform with catalog-backed identity:

```python
@pytest.mark.parametrize(
    ("platform", "raw", "asset_kind", "expected"),
    [
        ("service", "order-service", "SERVICE", "urn:ldp:staging:service:commerce:order-service"),
        ("http", "pricing.quote.v1", "API", "urn:ldp:staging:http:commerce:pricing.quote.v1"),
        ("postgres", "commerce.orders", "TABLE", "urn:ldp:staging:postgres:commerce:commerce.orders"),
        ("kafka", "commerce.orders.created", "TOPIC", "urn:ldp:staging:kafka:commerce:commerce.orders.created"),
        ("s3", "curated.orders_enriched", "OBJECT_DATASET", "urn:ldp:staging:s3:commerce:curated.orders_enriched"),
        ("snowflake", "gold.sales_analytics", "ANALYTIC_DATASET", "urn:ldp:staging:snowflake:commerce:gold.sales_analytics"),
    ],
)
def test_resolves_commerce_assets(platform, raw, asset_kind, expected, resolver):
    result = resolver.resolve(
        RawName("asset", raw, "SCENARIO"),
        commerce_context(platform=platform),
    )
    assert str(result.urn) == expected
    assert result.asset_kind == asset_kind
```

Add a query test asserting dataset nodes return their catalog kind and element nodes retain both `kind: "ELEMENT"` and `assetKind`.

**Step 2: Run tests to verify failure**

Expected: FAIL on vocabulary/catalog misses and missing `asset_kind`.

**Step 3: Extend the catalog**

Keep all scenario assets under owning system `commerce` so one proposal has one governance owner. Add `assetKind` to existing and new entries. Include at least:

- `order-service`, `order-enrichment-service`, `spark-sales-gold-job`
- `checkout.order-request.v1`, `customer.profile.v1`, `pricing.quote.v1`
- `commerce.orders`
- `commerce.orders.created`, `payments.authorized`
- `curated.orders_enriched`
- `reference.customer_master`, `reference.product_dimension`
- `gold.sales_analytics`

Add their exact elements and aliases. Expand vocabulary platforms with `service`, `http`, `postgres`, `kafka`, `s3`, and keep `snowflake`.

**Step 4: Return catalog kind from resolution and query**

Add `asset_kind: str` to `ResolvedName`. Give `QueryService` the catalog payload and return:

```python
{
    "urn": urn,
    "system": parsed.system,
    "kind": "ELEMENT" if parsed.element else asset_kind,
    "assetKind": asset_kind,
}
```

Do not infer an asset kind from a name or URI.

**Step 5: Run resolver and query tests**

Expected: PASS, including all original identity tests.

**Step 6: Commit**

```bash
git add fixtures/catalog apps/api/src/lineage_api/services/resolver.py apps/api/src/lineage_api/services/query.py apps/api/src/lineage_api/dependencies.py apps/api/tests/services
git commit -m "feat: catalog commerce service and data assets"
```

---

### Task 3: Generalize static analysis for multi-platform and multi-source assets

**Files:**
- Modify: `apps/api/src/lineage_api/domain/evidence.py`
- Modify: `apps/api/src/lineage_api/services/sca.py`
- Modify: `apps/api/tests/services/test_sca.py`

**Step 1: Write failing analyzer tests using a temporary repository**

Use source shaped like:

```python
checkout = read_asset(
    "checkout.order-request.v1",
    platform="http",
    elements=["order_id", "quantity"],
)
pricing = read_asset(
    "pricing.quote.v1",
    platform="http",
    elements=["unit_price"],
)
write_asset(
    "commerce.orders",
    platform="postgres",
    sources=[checkout, pricing],
    join_keys=[],
    mappings={
        "order_id": "checkout.order_id",
        "quantity": "checkout.quantity",
        "unit_price": "pricing.unit_price",
        "total_amount": "pricing.unit_price * checkout.quantity",
    },
)
```

Assert that:

- both sources resolve with their per-call platform;
- `total_amount` receives one exact edge from `unit_price` and one from `quantity`;
- the transform citation points to the output mapping;
- existing `read_dataset`/`write_dataset` behavior is byte-identical;
- `join_keys` are preserved in the SCA evidence document.

**Step 2: Run the analyzer tests and confirm failure**

Expected: FAIL because `read_asset`, `write_asset`, per-call platforms, qualified references, and joins are unsupported.

**Step 3: Implement the minimal generalized matcher**

Retain the old call names and add:

```python
READ_CALLS = {"read_dataset", "read_asset"}
WRITE_CALLS = {"write_dataset", "write_asset"}
QUALIFIED_REFERENCE = re.compile(
    r"(?P<binding>[A-Za-z_][A-Za-z0-9_]*)\.(?P<element>[A-Za-z_][A-Za-z0-9_]*)"
)
```

Use `dataclasses.replace(resolver_context, platform=platform_keyword)` per call. Resolve every binding in `sources`, emit one `ScaEdgeEvidence` per referenced source element, and preserve the legacy unqualified-token fallback for `payments-pipeline`.

Extend `ScaEvidenceFile` with a deterministic `joins` tuple and include it in `as_dict()`/`to_bytes()`.

**Step 4: Run the analyzer suite**

Expected: PASS for new and existing tests.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/domain/evidence.py apps/api/src/lineage_api/services/sca.py apps/api/tests/services/test_sca.py
git commit -m "feat: analyze multi-source service data flows"
```

---

### Task 4: Add the three application source fixtures and golden lineage

**Files:**
- Create: `fixtures/repositories/order-service/app.py`
- Create: `fixtures/repositories/order-enrichment-service/stream.py`
- Create: `fixtures/repositories/spark-sales-gold/job.py`
- Create: `fixtures/scenarios/commerce/input.json`
- Create: `fixtures/scenarios/commerce/expected-lineage.json`
- Create: `apps/api/tests/services/test_commerce_sca.py`

**Step 1: Write the failing golden test**

Analyze all three repositories with fixed digests and assert their sorted lineage tuples and joins exactly match `expected-lineage.json`. Explicitly assert the four-hop `unit_price` path and `orders.order_id = payments.order_id` join.

**Step 2: Run the test to verify it fails**

Expected: FAIL because the fixtures do not exist.

**Step 3: Write readable fixture programs**

Use declarations only; the analyzer reads them, while runtime behavior belongs to adapters:

```python
# order-service/app.py
request = read_asset("checkout.order-request.v1", platform="http", elements=[...])
customer = read_asset("customer.profile.v1", platform="http", elements=[...])
pricing = read_asset("pricing.quote.v1", platform="http", elements=[...])
write_asset("commerce.orders", platform="postgres", sources=[request, customer, pricing], mappings={...})
write_asset("commerce.orders.created", platform="kafka", sources=[request, customer, pricing], mappings={...})
```

The enrichment fixture consumes two Kafka assets and writes S3 with `join_keys=["orders.order_id = payments.order_id"]`. The Spark fixture reads S3 and two Snowflake reference assets and writes the Snowflake gold dataset with customer/product join keys.

**Step 4: Add deterministic input data**

Use one order whose `unit_price * quantity` has an exact decimal result, one matching payment authorization, one customer row, and one product row. Do not include secrets or real identifiers.

**Step 5: Run the golden test**

Expected: PASS with exact line citations and no residue.

**Step 6: Commit**

```bash
git add fixtures/repositories fixtures/scenarios apps/api/tests/services/test_commerce_sca.py
git commit -m "feat: add commerce application fixtures"
```

---

### Task 5: Build pure deterministic service, Kafka, S3, and Spark adapters

**Files:**
- Create: `apps/api/src/lineage_api/domain/scenario.py`
- Create: `apps/api/src/lineage_api/services/scenario_adapters.py`
- Create: `apps/api/tests/services/test_scenario_adapters.py`

**Step 1: Write failing pure-function tests**

Assert the adapter result contains:

- an order row and `orders.created` Kafka envelope with matching business data;
- propagated `scenarioId`, `correlationId`, `traceparent`, message key, partition, and offset;
- an enrichment join on `order_id` and S3 metadata carrying the trace/scenario identity;
- OpenLineage START and COMPLETE events with three inputs, one output, schema facets, and column-lineage facets;
- a gold row where `gross_revenue == total_amount` for the single-order fixture;
- stable canonical checksums across repeated runs.

**Step 2: Run the tests and verify failure**

Expected: FAIL because scenario domain types and adapters are missing.

**Step 3: Implement frozen scenario domain types**

Define `ScenarioEvent`, `ScenarioArtifact`, `EvidenceDocument`, and `ScenarioExecution` dataclasses with `as_dict()` methods. Canonicalize JSON with sorted keys and compact separators before SHA-256 checksumming.

**Step 4: Implement the three adapters**

Expose one pure entry point:

```python
def execute_commerce_scenario(
    fixture: dict[str, Any],
    identity: ScenarioIdentity,
) -> ScenarioExecution:
    order = execute_order_service(fixture, identity)
    enriched = execute_enrichment_service(order, fixture, identity)
    gold = execute_spark_job(enriched, fixture, identity)
    return ScenarioExecution.from_stages(order, enriched, gold)
```

Use fixed event timestamps derived from a supplied clock sequence. Never sleep or access the network/filesystem inside these pure adapters.

**Step 5: Run the adapter tests**

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/api/src/lineage_api/domain/scenario.py apps/api/src/lineage_api/services/scenario_adapters.py apps/api/tests/services/test_scenario_adapters.py
git commit -m "feat: simulate commerce service execution"
```

---

### Task 6: Add the durable scenario ledger and reset behavior

**Files:**
- Modify: `apps/api/src/lineage_api/db.py`
- Modify: `apps/api/src/lineage_api/seed.py`
- Create: `apps/api/src/lineage_api/services/scenario_repository.py`
- Create: `apps/api/tests/services/test_scenario_repository.py`
- Modify: `apps/api/tests/test_seed.py`

**Step 1: Write failing persistence tests**

Test that:

- a scenario run, ordered events, and artifact references round-trip;
- `delivery_id` and `scenario_id` are unique;
- looking up a repeated delivery returns the original record;
- reset removes scenario tables before parent `runs`/`events` rows;
- table snapshots are deterministic.

**Step 2: Run tests to verify failure**

Expected: FAIL because scenario tables and repository do not exist.

**Step 3: Add schema and repository**

Create:

```sql
CREATE TABLE IF NOT EXISTS scenario_runs (
    scenario_id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    trace_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    proposal_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenario_events (
    scenario_id TEXT NOT NULL REFERENCES scenario_runs(scenario_id),
    sequence INTEGER NOT NULL,
    lane TEXT NOT NULL,
    stage TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    PRIMARY KEY(scenario_id, sequence)
);
```

Keep artifacts as immutable evidence references inside the scenario payload rather than duplicating object bytes in SQLite.

**Step 4: Run persistence and seed tests**

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/db.py apps/api/src/lineage_api/seed.py apps/api/src/lineage_api/services/scenario_repository.py apps/api/tests
git commit -m "feat: persist commerce scenario ledger"
```

---

### Task 7: Orchestrate evidence collection, confidence, and proposal creation

**Files:**
- Modify: `apps/api/src/lineage_api/services/evidence_store.py`
- Modify: `apps/api/src/lineage_api/services/consolidation.py`
- Create: `apps/api/src/lineage_api/services/scenario.py`
- Modify: `apps/api/src/lineage_api/dependencies.py`
- Create: `apps/api/tests/services/test_scenario.py`
- Modify: `apps/api/tests/services/test_consolidation.py`

**Step 1: Write failing service tests**

Assert that a successful run:

- writes one standard `events` row and one `runs` row;
- records all three execution stages with one correlation identity;
- stores separate `sca`, `otel`, `kafka`, `s3`, `openlineage`, and `scenario-artifact` objects;
- verifies each evidence object before consolidation;
- creates `HIGH`/`ELEMENT` field edges from matching SCA and runtime evidence;
- creates `SINGLE`/`DATASET` runtime-only interaction edges;
- creates exactly one commerce proposal;
- returns the same scenario/proposal with `outcome: DUPLICATE` on replay.

Add a failure test that injects an adapter exception after Kafka consumption, persists partial evidence, marks the run failed, and creates no proposal.

**Step 2: Run tests to verify failure**

Expected: FAIL because the orchestration service and evidence kinds are missing.

**Step 3: Permit explicit simulation evidence kinds**

Add only these named kinds to `ALLOWED_KINDS`: `otel`, `kafka`, `s3`, `openlineage`, and `scenario-artifact`.

**Step 4: Make dataset-scope runtime assertions valid**

In consolidation, count a complete runtime assertion as the `RUNTIME` mechanism at either scope. Preserve `corroboration: DATASET` for dataset scope and `ELEMENT` for element scope. A runtime-only edge therefore receives the existing contract's `SINGLE` band.

**Step 5: Implement `CommerceScenarioService`**

The service must:

1. Return an existing scenario before doing work when the fixed delivery ID exists.
2. Create standard event/run identities and a scenario ledger row.
3. Analyze the three repositories and store each SCA document.
4. Execute the pure adapters and store their raw evidence/artifacts.
5. Resolve every runtime asset through the pinned catalog.
6. Match each static field edge with runtime element evidence.
7. Add runtime-only service/resource relationships.
8. Verify stored checksums through `EvidenceStore.get()` before merging.
9. Create one proposal against the active pointer.
10. Persist the proposal ID and return a strict scenario response.

Do not derive field edges from the expected-lineage file; it is test data only.

**Step 6: Run scenario and consolidation tests**

Expected: PASS.

**Step 7: Commit**

```bash
git add apps/api/src/lineage_api/services apps/api/src/lineage_api/dependencies.py apps/api/tests/services
git commit -m "feat: collect commerce scenario lineage evidence"
```

---

### Task 8: Expose the scenario API

**Files:**
- Modify: `apps/api/src/lineage_api/main.py`
- Modify: `apps/api/src/lineage_api/api_models.py`
- Modify: `apps/api/tests/test_api.py`

**Step 1: Write failing API tests**

Cover:

```text
POST /api/scenarios/commerce/reset     200, empty scenario state
POST /api/scenarios/commerce/run       202 ACCEPTED, strict response
POST /api/scenarios/commerce/run       200 DUPLICATE on replay
GET  /api/scenarios/commerce           200 summary/list
GET  /api/scenarios/commerce/{id}      200 detail
GET  /api/scenarios/commerce/missing   404 SCENARIO_NOT_FOUND
```

Validate the accepted response against `scenario-run.schema.json`.

**Step 2: Run API tests to verify failure**

Expected: 404 for all new routes.

**Step 3: Add thin FastAPI handlers**

Handlers delegate to `AppServices.scenario`; they contain no business logic. The scenario reset intentionally invokes the prototype's global deterministic reset and must label that behavior in its response.

Add `SCENARIO_NOT_FOUND` to `NOT_FOUND_CODES`.

**Step 4: Run API tests**

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/lineage_api/main.py apps/api/src/lineage_api/api_models.py apps/api/tests/test_api.py
git commit -m "feat: expose commerce scenario api"
```

---

### Task 9: Prove the complete scenario through publication and impact

**Files:**
- Create: `tests/test_commerce_scenario.py`
- Modify: `apps/api/src/lineage_api/services/orchestration.py`
- Modify: `apps/api/tests/services/test_orchestration.py`

**Step 1: Write the failing end-to-end test**

The test must reset, run the commerce scenario, inspect the proposal, approve it through the existing review endpoint, then query:

```python
subject = "urn:ldp:staging:http:commerce:pricing.quote.v1#unit_price"
gold = "urn:ldp:staging:snowflake:commerce:gold.sales_analytics#gross_revenue"

lineage = client.get(
    f"/api/lineage/{quote(subject, safe='')}",
    params={"direction": "down", "depth": 5},
).json()
impact = client.post(
    "/api/impact",
    json={"subject": subject, "changeType": "COLUMN_TYPE_CHANGE", "depth": 5},
).json()

assert gold in {node["urn"] for node in lineage["nodes"]}
assert any(item["urn"] == gold for item in impact["affected"])
```

Also assert the active pointer advances once, all four field hops are `HIGH`/`ELEMENT`, the runtime-only service edges remain inspectable, and the scenario run finishes `PUBLISHED`.

**Step 2: Run the test and verify failure**

Expected: FAIL where approval cannot finalize the scenario run or a path edge is missing.

**Step 3: Make approval completion generic**

Keep the existing correlation-based run lookup. Ensure approval stages and finalizes a scenario-created run without coupling `OrchestrationService` to scenario fixture details. Scenario detail should derive current state from the standard run/proposal records.

**Step 4: Run the commerce and original walking-skeleton tests**

Run:

```bash
uv run --project apps/api --extra dev pytest tests/test_commerce_scenario.py tests/test_walking_skeleton.py -q
```

Expected: both PASS.

**Step 5: Commit**

```bash
git add tests/test_commerce_scenario.py apps/api/src/lineage_api/services/orchestration.py apps/api/tests/services/test_orchestration.py
git commit -m "test: prove commerce source-to-gold lineage"
```

---

### Task 10: Add typed frontend API support and query-aware navigation

**Files:**
- Modify: `apps/web/src/api/types.ts`
- Modify: `apps/web/src/api/client.ts`
- Modify: `apps/web/src/routing.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/components/layout/AppShell.tsx`
- Modify: `apps/web/src/App.test.tsx`
- Create: `apps/web/src/routing.test.tsx`

**Step 1: Write failing router and shell tests**

Assert that `/scenario` renders a Scenario Lab route, the navigation marks it active, browser/memory navigation preserves `?subject=...`, and `useSearch()` returns the query string.

**Step 2: Run frontend tests to verify failure**

Expected: FAIL because scenario types/client/route and query-aware routing do not exist.

**Step 3: Add strict TypeScript models**

Create `ScenarioRun`, `ScenarioEvent`, `ScenarioArtifact`, and `ScenarioEvidence` interfaces matching the JSON Schema exactly. Extend lineage node typing with catalog `assetKind` values.

Add client methods:

```typescript
scenario: (signal?: AbortSignal) => request<ScenarioRun | null>("/api/scenarios/commerce", {}, signal),
runScenario: () => request<ScenarioRun>("/api/scenarios/commerce/run", { method: "POST" }),
resetScenario: () => request<ScenarioRun | null>("/api/scenarios/commerce/reset", { method: "POST" }),
```

**Step 4: Make the local router location-aware**

Store `{ pathname, search }`, parse internal destinations through `URL`, preserve browser back/forward behavior, and expose `useSearch()`. Route matching must use pathname only.

**Step 5: Add Scenario Lab navigation and route placeholder**

Add `/scenario` between Operations and Review queue. Render a temporary semantic heading until the page task replaces it.

**Step 6: Run tests and build**

Expected: PASS.

**Step 7: Commit**

```bash
git add apps/web/src/api apps/web/src/routing.tsx apps/web/src/routing.test.tsx apps/web/src/App.tsx apps/web/src/components/layout/AppShell.tsx apps/web/src/App.test.tsx
git commit -m "feat: wire scenario lab navigation"
```

---

### Task 11: Build the Scenario Lab topology and evidence inspector

**Files:**
- Create: `apps/web/src/pages/ScenarioLabPage.tsx`
- Create: `apps/web/src/components/scenario/ScenarioTopology.tsx`
- Create: `apps/web/src/components/scenario/ScenarioStage.tsx`
- Create: `apps/web/src/components/scenario/InteractionInspector.tsx`
- Create: `apps/web/src/components/scenario/ScenarioLegend.tsx`
- Create: `apps/web/src/pages/ScenarioLabPage.test.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/styles/global.css`

**Step 1: Write failing component tests**

Mock the scenario API and assert the page exposes:

- the “Run commerce scenario” action;
- all three stages and all major assets;
- business, execution, and collection lanes;
- raw identifier, canonical URN, mechanism, checksum, and confidence after selecting an interaction;
- proposal link after completion;
- error/retry state with the API correlation ID;
- a non-animated complete representation under reduced motion.

**Step 2: Run the page test and verify failure**

Expected: FAIL because Scenario Lab components do not exist.

**Step 3: Implement the semantic component structure**

Use buttons for selectable relationships, ordered lists for event sequences, headings for stages, and an `aria-live="polite"` status. The visual topology can use CSS grid and SVG connectors, but an accessible relationship list must expose identical information.

Reuse existing design tokens. Add asset-kind treatments for service, API, database, topic, object storage, reference, Spark, and gold dataset. Do not use a component framework or add dependencies.

**Step 4: Implement query/mutation behavior**

Use TanStack Query for current scenario and mutations for run/reset. Invalidate `scenario`, `overview`, `runs`, and `proposals` after a successful run.

**Step 5: Run page tests and production build**

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/web/src/pages/ScenarioLabPage.tsx apps/web/src/pages/ScenarioLabPage.test.tsx apps/web/src/components/scenario apps/web/src/App.tsx apps/web/src/styles/global.css
git commit -m "feat: add guided commerce scenario lab"
```

---

### Task 12: Add deterministic event replay and lineage/impact shortcuts

**Files:**
- Create: `apps/web/src/hooks/useScenarioReplay.ts`
- Create: `apps/web/src/hooks/useScenarioReplay.test.ts`
- Modify: `apps/web/src/pages/ScenarioLabPage.tsx`
- Modify: `apps/web/src/pages/LineageExplorerPage.tsx`
- Modify: `apps/web/src/pages/LineageExplorerPage.test.tsx`
- Modify: `apps/web/src/components/lineage/LineageCanvas.tsx`
- Modify: `apps/web/src/components/lineage/lineageLayout.ts`

**Step 1: Write failing replay and preload tests**

Using fake timers, assert replay reveals events in sequence, can be skipped, completes immediately when reduced motion is enabled, and never hides already available information from assistive technology.

Assert `/lineage?subject=<pricing-urn>&direction=down&depth=5` initializes the explorer with those values. Verify typed asset kinds appear in node labels and the larger graph layout is deterministic.

**Step 2: Run the tests to verify failure**

Expected: FAIL because replay/preload support does not exist.

**Step 3: Implement replay as presentation state only**

The backend run is already complete. Label the sequence “Recorded execution replay.” Use the event timestamps/order returned by the API, a short client interval, a visible “Show complete run” control, and reduced-motion detection. Never issue per-stage mutation requests.

**Step 4: Implement lineage and impact shortcuts**

Link to the encoded pricing URN with depth 5. Initialize explorer state from validated query parameters; fall back to the existing payments subject for absent/invalid parameters.

**Step 5: Run frontend tests and build**

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/web/src/hooks apps/web/src/pages apps/web/src/components/lineage
git commit -m "feat: replay scenario and trace source to gold"
```

---

### Task 13: Document operation, scope, and newly exposed ambiguities

**Files:**
- Modify: `README.md`
- Modify: `docs/prototype-coverage.md`
- Modify: `docs/prd-ambiguities.md`
- Modify: `tests/test_documentation.py`

**Step 1: Write failing documentation assertions**

Require README text for Scenario Lab, all three applications, Docker-free simulation boundaries, review/publish instructions, and the pricing-to-gold impact walkthrough.

**Step 2: Run documentation tests to verify failure**

Expected: FAIL on missing scenario documentation.

**Step 3: Update documentation**

Document:

1. `make dev` and `/scenario` usage.
2. The difference between simulated adapters and real infrastructure.
3. Evidence emitted at each collection boundary.
4. The manual review and fenced publication step.
5. The full-chain impact query.

Add ambiguity entries for service/API asset representation in dataset-shaped URNs, Kafka topic/schema identity, S3 prefix versus object identity, OpenLineage facet version, cross-service ownership, and the absence of a PRD rule for aggregating independent runtime sources. Record the chosen reversible prototype decisions.

**Step 4: Run documentation tests**

Expected: PASS.

**Step 5: Commit**

```bash
git add README.md docs/prototype-coverage.md docs/prd-ambiguities.md tests/test_documentation.py
git commit -m "docs: explain commerce lineage simulation"
```

---

### Task 14: Complete repository and browser verification

**Files:**
- Modify only if verification exposes a defect.

**Step 1: Reset and run all automated checks**

Run:

```bash
make reset
make test
make build
npm audit --audit-level=moderate
git diff --check
```

Expected: all backend/frontend/documentation tests pass, production build succeeds, audit reports zero vulnerabilities, and diff check is clean.

**Step 2: Start the local application**

Run:

```bash
make dev
```

Expected: FastAPI at `127.0.0.1:8000`, Vite at `127.0.0.1:5173`.

**Step 3: Exercise the complete Chromium walkthrough**

Using `@playwright` CLI:

1. Open Scenario Lab.
2. Run the commerce scenario.
3. Inspect each stage, runtime mechanism, artifact, URN, and checksum.
4. Confirm the recorded replay and “Show complete run” behavior.
5. Open and approve the proposal with a rationale.
6. Return to Scenario Lab and open the published lineage shortcut.
7. Confirm the four-hop pricing-to-gold path.
8. Run impact analysis and confirm the gold field is affected.
9. Check keyboard behavior, a 390px mobile viewport, console errors, and failed network requests.

**Step 4: Re-run affected tests after any fix**

Use `@systematic-debugging` before changing code for any observed defect, then repeat the relevant automated and browser checks.

**Step 5: Commit final verification fixes, if any**

```bash
git add <only verified files>
git commit -m "fix: finalize commerce scenario walkthrough"
```

**Step 6: Confirm clean handoff state**

Run:

```bash
git status --short
git log --oneline -5
```

Expected: clean worktree on `codex/lineage-prototype` with all scenario commits present.
