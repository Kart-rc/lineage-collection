# Service Interactions Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture service-to-service API calls with their operation identity and field-level request/response contracts — the second lineage plane the product prototype models, which no current contract can express.

**Architecture:** This is genuinely net-new. The prototype separates two graphs explicitly (`// batch/stream data movement lives in Reads & writes — only synchronous calls here`): datastore lineage, and service interactions. The existing `runtime-observation.schema.json` is dataset-centric — its full property set is `sourceDatasets, targetDataset, sourceFields, targetField, transform, edgeType, granularity, mechanism, exact, traceId, spanId, observedAt, parserContract` — with no operation, channel, request/response schema, or latency. A REST call cannot be encoded in it. So this plan adds a parallel contract and two producers: static extraction from repository sources, and enrichment of OTel spans that already carry the data.

**Tech Stack:** Python 3.12, tree-sitter (already used by the Java cell), pytest.

## Global Constraints

- **Separate contract, separate plane.** Do not extend `runtime-observation.schema.json`. Interactions are not dataset lineage and conflating them would corrupt the corroboration rules.
- **Closed channel vocabulary:** `REST`, `GRPC`, `GRAPHQL`, `ASYNC_EVENT`.
- **Field contracts come from declarations, never from payloads.** Request/response fields are read from OpenAPI, protobuf, GraphQL SDL, or annotated handler signatures. **No observation ever carries a field value** — the metadata-only boundary applies here exactly as it does to dataset lineage.
- **Classification is declared.** A field is `PII` only if the repository or catalog says so. The analyzer never infers sensitivity from a name like `email`.
- **Unresolvable is residue.** A call whose target service cannot be identified is typed residue, not a guessed edge.
- Test command prefix: `uv run --project apps/api python -m pytest`

---

## File Structure

| File | Responsibility |
|---|---|
| `packages/contracts/interaction-observation.schema.json` (create) | The contract for one observed or declared service-to-service call. |
| `apps/api/src/lineage_api/domain/interactions.py` (create) | The domain model and its validation. |
| `apps/api/src/lineage_api/services/java_interaction_sca.py` (create) | Static extraction: inbound endpoints and outbound call sites from Java sources. |
| `apps/api/src/lineage_api/runtime/adapters/otel_interactions.py` (create) | Map spans carrying `http.route` / `rpc.method` / `peer.service` into interaction observations. |
| Tests mirroring each of the above under `apps/api/tests/`. |

---

### Task 1: The interaction contract

**Interfaces:**
- Produces: `interaction-observation.schema.json` and `InteractionObservation` with required fields:

```
schemaVersion, observationId, fromService, toService, channel, operation,
mechanism (SCA | RUNTIME), exact, observedAt
```

optional: `requestFields[]`, `responseFields[]` (each `{name, type, classification}`), `latencyP99Ms`, `traceId`, `spanId`.

- [ ] **Step 1:** Write `apps/api/tests/domain/test_interactions.py` asserting: a valid observation round-trips; an unknown channel raises; a field carrying a `value` key is rejected (metadata-only boundary); `classification` defaults to `NONE` rather than being guessed.
- [ ] **Step 2:** Run; confirm failure.
- [ ] **Step 3:** Write the schema and the domain model.
- [ ] **Step 4:** Run; green.
- [ ] **Step 5:** `git commit -m "feat: add the service interaction observation contract"`

---

### Task 2: Static extraction of inbound endpoints

Extract what a Java service *exposes*: `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@RequestMapping` on `@RestController` / `@Controller` classes, with the declared path and the handler's parameter and return types.

**Interfaces:**
- Produces: `read_inbound_endpoints(sources: Mapping[str, str]) -> tuple[InboundEndpoint, ...]`
- `InboundEndpoint(service: str, channel: str, operation: str, handler: str, request_fields: tuple, response_fields: tuple, path: str, line: int)`

`operation` is `"{VERB} {path}"` — e.g. `GET /owners/{ownerId}`, matching the prototype's `op` field.

- [ ] **Step 1:** Write tests against a fixture controller covering: a plain `@GetMapping("/owners/{id}")`; class-level `@RequestMapping` prefix composition; a method with no mapping annotation (ignored); a mapping built from a constant (residue `dynamic-route`).
- [ ] **Step 2–4:** Red, implement with tree-sitter reusing the Java cell's existing parse helpers, green.
- [ ] **Step 5:** `git commit -m "feat: extract inbound HTTP endpoints from Java controllers"`

**Note on Petclinic:** its controllers are `@Controller` returning view names, not `@RestController` returning JSON. Inbound extraction will find the routes but the response "fields" are a view name, not a schema. Report that honestly as `response_fields=()` rather than inventing a body — and expect `spring-petclinic-rest` to be the fixture that exercises real JSON contracts.

---

### Task 3: Static extraction of outbound calls

Extract what a service *calls*: `RestTemplate`, `WebClient`, and declarative `@FeignClient` interfaces.

**Interfaces:**
- Produces: `read_outbound_calls(sources: Mapping[str, str]) -> tuple[OutboundCall, ...]`

Resolution rule: a `@FeignClient(name = "customer-service")` names its target directly and is exact. A `RestTemplate` call with a literal URL yields the host; a URL built from a variable is residue `dynamic-endpoint`. **The target service is resolved through the catalog, never guessed from a hostname** — this is the same discipline as dataset resolution.

- [ ] **Step 1:** Tests for: an exact `@FeignClient`; a literal `RestTemplate` URL; a dynamically built URL (residue); an unresolvable target (residue `unresolved-service`).
- [ ] **Step 2–4:** Red, implement, green.
- [ ] **Step 5:** `git commit -m "feat: extract outbound service calls from Java sources"`

---

### Task 4: OTel span enrichment

Replace the host-only `CONNECTIVITY` mapping at `runtime/adapters/otel.py:255-262` — which currently discards everything but the hostname — with a real interaction observation.

**Interfaces:**
- Produces: `normalize_interaction_span(span, profile) -> tuple[dict | None, AdapterIssue | None]`

Mapping: `http.request.method` + `http.route` → `REST` with operation `"{METHOD} {route}"`; `rpc.method` + `rpc.service` → `GRPC`; `graphql.operation.name` → `GRAPHQL`; `messaging.operation` → `ASYNC_EVENT`. `peer.service` identifies the target; absent it, residue.

- [ ] **Step 1:** Tests per channel plus one asserting that a span with no route and no rpc method yields `OTEL_INTERACTION_NOT_MAPPABLE` rather than a host-only guess.
- [ ] **Step 2–4:** Red, implement, green. Leave the existing `CONNECTIVITY` path in place for spans that genuinely carry only an address.
- [ ] **Step 5:** `git commit -m "feat: map OTel spans to service interaction observations"`

---

### Task 5: The interaction graph surface

- [ ] Aggregate interactions into the prototype's three altitudes: service→service, application→application, domain→domain. Application and domain membership is **catalog-owned** — add `application` and `domain` to catalog dataset and service records rather than inferring them from names.
- [ ] Expose the matrix through the product API alongside the dataset graph.
- [ ] Test that the same set of interactions rolls up consistently at all three altitudes and that the totals reconcile.

---

## Final verification

- [ ] Full suite green
- [ ] A fixture service pair produces a REST interaction with typed request/response fields
- [ ] No interaction observation anywhere carries a field *value*
- [ ] `docs/architecture/lineage-platform-target.md` gains the interactions plane — it is currently absent from the target diagram entirely
