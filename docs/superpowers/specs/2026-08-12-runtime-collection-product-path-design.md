# Runtime lineage collection on the product path — design

Date: 2026-08-12
Status: Approved for planning

## 1. Problem

The runtime verification loop exists as a complete library — `run_runtime_stage`
plans tests from SCA edges, executes the analysed code under recording
instrumentation (Python seam and Java Spring Data proxy seam), and verifies the
observations against the static claim. But it has exactly two callers: a test
and `scripts/verify_prototype_alignment.py`. The product collection path still
hardcodes `runtimeStatus: NOT_PROVIDED` (`application/repository_collection.py:306`),
and the runtime intake plane (sessions, leases, closed-schema observation
intake) has never carried traffic from a real producer — only JSON fixtures.

This design finishes the unfinished Task 4 of
`docs/superpowers/plans/2026-08-12-runtime-on-the-collection-path.md`: wire the
runtime stage onto the product collection path, routed through the session
plane, for both the Python and Java seams.

## 2. Decisions taken during brainstorming

| Question | Decision |
| --- | --- |
| Primary goal | Wire the existing runtime verification stage onto the product collection path (not emitters, not 24/24, not query logs) |
| Execution model | Per-collection opt-in flag, in-process execution; isolated-worker execution deferred and documented as a limit |
| Evidence path | Through the session plane: `grant_session` → `observe` → `close` → consolidation |
| Seam scope | Python and Java, both in this plan |
| Acceptance bar | Fixture proof (payments-pipeline → CORROBORATED, HIGH/92) plus real petclinic Java corroboration, both via `repository_collection` |
| Orchestration shape | Approach B: `repository_collection` (application layer) owns the session lifecycle; the stage stays a pure library |

## 3. Architecture and data flow

`RepositoryCollectionService` gains `runtime_execution: bool = False` on the
collection request. Default off preserves today's behaviour byte-for-byte
(`runtimeStatus: NOT_PROVIDED`, reason `not-requested`). There is no env var
and no global config: runtime execution is consent per collection.

When opted in, after the SCA stage returns its edges:

1. **Grant** — `RuntimeLineageService.grant_session` with the collection's
   repo, environment, and the snapshot's artifact digest; session scope is the
   set of dataset URNs appearing in the SCA edges. Existing guards apply
   unchanged: non-production-only, kill switch, TTL, HMAC token. A denied
   grant skips runtime with reason `session-denied`; collection continues.
2. **Execute** — `run_runtime_stage(edges, ...)` with the catalog element
   resolver, in-process. Language dispatch: Python sources run under
   `RecordingInstrumentation`; Java repos run the Spring Data proxy harness
   (`generate_harness` → execute → `parse_observations` → `verify_java_lineage`)
   guarded by the `LINEAGE_JAVA_HOME` probe. A missing JVM reports reason
   `jvm-unavailable` for the Java half; the Python half still runs. A mixed
   repo yields observations from both seams into the same session.
3. **Emit** — each observed edge is mapped to an SDK-mechanism observation
   payload and fed through `RuntimeLineageService.observe`, exercising the
   closed-schema allowlist, metadata-only assertion, artifact-digest match,
   scope check, idempotency, and monotonic sequence on real traffic.
4. **Close** — `close(session)` produces the session manifest, validated with
   `runtime_window_manifest_errors` before any evidence is used.
5. **Consolidate** — accepted observations flow through
   `merge_runtime_observation` (unchanged: runtime never invents an edge; it
   corroborates by exact URN match). The hardcoded status at
   `repository_collection.py:306` is replaced by the stage verdict:
   `CORROBORATED | PARTIALLY_CORROBORATED | NOT_PROVIDED`. Per-edge liveness
   is attached. Corroborated edges band to `HIGH` → VERIFIED 92% through the
   existing `derive_band` / `project_confidence`, untouched.

### 3.1 Where schema and URN matching happen

Matching happens at two points, both catalog-pinned, neither on the wire:

- **Stage time (verdict).** `verify_static_lineage` resolves each raw
  `ObservedEdge` (plain dataset/element names) through the pinned catalog into
  URNs and compares them with the SCA edges' URNs, classifying each edge
  `corroborated | static_only | runtime_only`. Unknown datasets or elements
  fail to resolve and cannot corroborate; the resolver never guesses.
- **Consolidation time (join).** The intake plane does no catalog resolution —
  observations carry wire identity (`namespace/name`, field names). At
  consolidation, `_resolve_runtime_dataset` resolves wire identity through the
  catalog (refusing to mint identity the catalog does not own) and matches by
  exact URN equality against existing SCA edges. Only an exact ELEMENT-scoped
  match lifts a band.

The double resolution is intentional: the verdict reports what the run proved;
the consolidation join is the only writer to the edge ledger and re-derives
identity from the catalog rather than trusting the stage.

### 3.2 Shared URN and resolver invariant

No new library is needed. `domain/urns.py` is the single URN grammar and
`services/resolver.py` the single catalog-pinned resolver; SCA compilation,
the stage's `catalog_element_resolver`, and consolidation all already resolve
through them. The design adds the invariant explicitly:

> Within one collection, the SCA stage, the runtime stage's element resolver,
> and the consolidation join MUST be constructed from the same catalog
> snapshot and resolver version. The orchestration in `repository_collection`
> passes one resolver instance (or one snapshot handle) to all three. A
> mismatch is a defect, not a soft degradation.

Transform-text rendering differences between cells (`transformConflict` at
composition) are out of scope: runtime corroboration matches URNs, not
transform strings.

## 4. Components

1. **`application/runtime_stage.py`** — grows a language dispatcher over the
   snapshot's sources: Python seam (existing path) always; Java harness when
   `.java` sources are present and the JVM probe succeeds. Both seams verify
   against the same SCA edge set and merge into one `RuntimeStageResult`. The
   stage remains a pure library: no session, no service calls.
2. **`application/runtime_emission.py` (new)** — pure mapping from stage
   observations to SDK-mechanism payloads conforming to
   `packages/contracts/runtime-observation.schema.json`: wire dataset
   identities, source/target fields, `edgeType`, granularity `ELEMENT`, the
   session's artifact digest, monotonic sequence. Transform text is included
   only if it passes the intake plane's metadata-only assertion; otherwise the
   transform is dropped and the edge kept — corroboration matches on URNs, so
   nothing that matters is lost and query text never transits intake.
3. **`application/repository_collection.py`** — the orchestration block of §3,
   the `runtime_execution` request parameter, the derived `runtimeStatus`, and
   the single resolver/snapshot handle shared across SCA, stage, and
   consolidation.
4. **`dependencies.py`** — inject the already-constructed
   `RuntimeLineageService` into `RepositoryCollectionService`. No new service
   objects.
5. **Status document** — `runtimeStatus ∈ {CORROBORATED,
   PARTIALLY_CORROBORATED, NOT_PROVIDED}` plus `runtimeReasons`, a list drawn
   from a closed vocabulary mirroring the SCA residue discipline:
   `not-requested`, `session-denied`, `jvm-unavailable`, `execution-failed`,
   `observation-rejected`, `session-incomplete`. No free-text reasons.

## 5. Error handling

One rule applied everywhere: **runtime failure degrades to `NOT_PROVIDED`
with a closed-vocabulary reason; it never fails the collection and never
touches the SCA result.**

- Opt-in absent → `not-requested`; output byte-identical to today.
- Grant denied (production environment, kill switch, bad scope) →
  `session-denied` carrying the policy reason.
- An entry point raising during execution → recorded per edge by the existing
  stage machinery; surviving corroborations yield `PARTIALLY_CORROBORATED`.
- JVM absent → Java half skipped with `jvm-unavailable`; Python half
  unaffected.
- Any observation rejected at intake → the whole session's evidence is
  discarded. The manifest arithmetic already enforces this (`COMPLETE` admits
  no loss; consolidation ignores non-`COMPLETE` outcomes), so fail-closed
  behaviour is inherited, not rebuilt. Status `NOT_PROVIDED` +
  `observation-rejected`; a rejected payload is a bug to fix, not evidence to
  salvage.
- Manifest validation errors → evidence unused, `session-incomplete`.

## 6. Testing

Unit (TDD, per component):

- **Observation mapping** — every `ObservedEdge` shape the two seams produce
  maps to a payload `RuntimeLineageService.observe` accepts (closed schema,
  metadata-only, sequence). Includes the transform-drop case: a
  projection-SQL transform yields an accepted payload without the transform,
  same edge identity.
- **Language dispatch** — Python-only repo runs only the Python seam;
  Java-only runs only the harness; mixed runs both into one result;
  `LINEAGE_JAVA_HOME` unset yields `jvm-unavailable` with Python results
  unaffected.
- **Orchestration** — one test per closed-vocabulary reason, each proving the
  same two invariants: collection completes and SCA output is untouched. The
  grant-denied test uses a real `grant_session` denial (production
  environment), not a mock.
- **Resolver invariant** — constructing the stage and consolidation from
  mismatched snapshots fails loudly.

Integration (the acceptance bar):

1. **Fixture proof** — `repository_collection` over
   `fixtures/repositories/payments-pipeline` with `runtime_execution=True`:
   `runtimeStatus=CORROBORATED`; a real session manifest with
   `outcome=COMPLETE`; every corroborated edge holds both `SCA` and
   ELEMENT-scoped `RUNTIME` assertions; band `HIGH`; display VERIFIED 92%;
   per-edge liveness attached.
2. **Petclinic proof** — `repository_collection` over the real
   `spring-petclinic-microservices` checkout: Java runtime corroboration on
   the 9 SCA edges. Skips when the checkout or JVM is absent so CI without a
   JVM stays green while a full environment proves the claim.

Regression guarantees:

- Default-off collection output is byte-identical to today (golden
  comparison).
- `scripts/verify_real_repo_confidence.py` still reports 20/24 — the SQL cell
  and Plane C are untouched.
- `scripts/verify_prototype_alignment.py` still passes 18/18 — the stage
  library's behaviour is unchanged; only its callers grow.

## 7. Out of scope

- Isolated-worker execution (in-process + per-collection opt-in is a
  documented limit; isolation is a follow-on plan).
- Real emitters — Spark listener, OTel Collector packaging, installable SDK
  (plan R4/R7).
- The AWS lane (Kinesis intake, B6/I6 durable stage, Fargate worker).
- Query-log / warehouse-history collection.
- The 4 remaining real-repo events (all SCA-side: Hive multi-insert parsing,
  nested CTEs, case-insensitive identifier resolution).
- The `HIGH` band ceiling — three-mechanism `HIGHEST` remains definitionally
  out of reach for SCA+runtime and is unchanged here.
