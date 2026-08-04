# PRD Ambiguity Register

Reviewed against component PRDs L01–L16, the implementation-readiness review, and `Architecture Diagrams v3.dc.html`. No enterprise value below was inferred. Every local choice is reversible and isolated behind a fixture, setting, or component boundary.

Classifications: **Enterprise context seam** needs organization-specific input; **Specification gap** needs normative authoring; **Cross-document inconsistency** needs one source of truth; **Prototype assumption** is a scoped local decision, not production policy.

## Enterprise context seams

| ID | Classification | Source | Impact | Prototype decision | Production owner |
|---|---|---|---|---|---|
| CTX-01 | Enterprise context seam | Readiness review, L01 | Catalog import schema and delivery mechanism are unknown. | Load the checked-in `catalog-snapshot-v1.json` through the resolver interface. | Catalog platform |
| CTX-02 | Enterprise context seam | Readiness review, L01 | Initial environment, platform, and system vocabularies are unknown. | Fixture vocabulary is only `staging`, `snowflake`, and `payments`; unknowns are rejected. | Data architecture |
| CTX-03 | Enterprise context seam | Readiness review, L01 | Estate temporary/staging naming patterns cannot be normalized safely. | No temp-name guesses; unresolved names become explicit residue or quarantine. | Data governance |
| CTX-04 | Enterprise context seam | Readiness review, L01 | Alias decision approvers and governance flow are unknown. | Catalog aliases are read-only; the prototype provides no alias mutation. | Data governance |
| CTX-05 | Enterprise context seam | Readiness review, L02 | Jenkins deploy payload and signing contract are absent. | Demonstrate only a canonical HMAC-signed repository push; deploy intake is deferred. | Developer platform |
| CTX-06 | Enterprise context seam | Readiness review, L02 | GitHub App organization and installation scope are absent. | Use a local signed delivery fixture and never contact GitHub. | Developer platform |
| CTX-07 | Enterprise context seam | Readiness review, L04 | The production SQL dialect set is unknown. | Implement the fully deterministic Python AST seed pack only; no SQL parser selection is guessed. | Data platform owners |
| CTX-08 | Enterprise context seam | Readiness review, L04 | Config-manifest conventions are unknown. | Resolver config is empty and dynamic names are retained as residue. | Application platform |
| CTX-09 | Enterprise context seam | Readiness review, L05 | Bedrock gateway endpoint, authentication, and model allow-list are unknown. | No external model call; retain LLM assertion/evidence seams for a future adapter. | AI platform and security |
| CTX-10 | Enterprise context seam | Readiness review, L06 | Spark platform and OpenLineage listener installation path are unknown. | Use deterministic element-level runtime fixture assertions. | Data runtime platform |
| CTX-11 | Enterprise context seam | Readiness review, L06 | CI/test-harness entry points for session grants are unknown. | The seeded run creates a complete local runtime session fixture directly. | Test automation service |
| CTX-12 | Enterprise context seam | Readiness review, L06 | OTel collector topology and ownership are unknown. | No collector is started; runtime enters through the adapter boundary. | Observability platform |
| CTX-13 | Enterprise context seam | Readiness review, L08 | Evidence retention durations by compliance class are open. | Local evidence is retained until explicit demo reset; no time-based deletion policy is invented. | Compliance |
| CTX-14 | Enterprise context seam | Readiness review, L09 | TAS system-to-reviewer-group field mapping is unknown. | Display local owner labels as `team-{system}`; do not treat them as authorization. | TAS and identity governance |
| CTX-15 | Enterprise context seam | Readiness review, L09 | Catalog PII/classification flags for auto-publish exclusion are unknown. | Keep all seeded parser-exact edges behind manual review. | Catalog governance and privacy |
| CTX-16 | Enterprise context seam | Readiness review, L12/L15 | Organization paging and severity conventions are unknown. | Surface typed UI/API errors locally; send no external page or notification. | SRE operations |
| CTX-17 | Enterprise context seam | Readiness review, L12/L15 | CloudTrail and Config baseline/retention are unknown. | Use the local immutable audit table only; document that it is not an org audit control. | Cloud security |

## Remaining specification gaps

| ID | Classification | Source | Impact | Prototype decision | Production owner |
|---|---|---|---|---|---|
| G-L01-1 | Specification gap | Readiness review, L01 | Per-platform case and quote tables are not authored as data. | Apply only fixture-backed normalization and catalog aliases; add platforms through versioned resolver data. | Resolver maintainers |
| G-L03-3 | Specification gap | Readiness review, L03 | ASL stage input/output definitions are missing. | Record a synchronous local stage ledger with explicit JSON details; do not present it as deployable ASL. | Workflow platform |
| G-L04-1 | Specification gap | Readiness review, L04 | Complete matcher tables per framework are absent. | Treat the seeded Python `read_dataset`/`write_dataset` matcher as the template pack. | Extraction team |
| G-L05-1 | Specification gap | Readiness review, L05 | Prompt templates and tier-specific structured-output schemas are not supplied. | Make no model call and generate no synthetic LLM claim. | AI platform and lineage governance |
| G-L05-2 | Specification gap | Readiness review, L05 | Per-repository token and cost caps are open. | Preserve configuration ownership; LLM execution is disabled. | Cost governance |
| G-L06-1 | Specification gap | Readiness review, L06 | OTel span-attribute-to-`RawName` mapping is not authored. | Inject schema-valid fixture assertions after the resolver boundary. | Runtime observation team |
| G-L07-3 | Specification gap | Readiness review, L07 | The full contradiction predicate/transform normalization list is incomplete. | Use the tested minimal normalization rule for the seed and retain differing exact transforms as conflict. | Confidence model owner |
| G-L09-2 | Specification gap | Readiness review, L09 | Calibration corpus fields are described but no strict JSON Schema exists. | Store approval and audit records; defer corpus emission rather than invent its contract. | Governance analytics |
| G-L11-2 | Specification gap | Readiness review, L11 | Review SPA information architecture has no wireframes. | Use an evidence-control-room IA: Operations, Review, Lineage, Runs; validate it with accessible component and browser tests. | Product design and UX |
| PA-01 | Prototype assumption | Local architecture decision | AWS services would make the prototype costly and non-local. | Map DynamoDB to SQLite, S3 Object Lock to a write-once local store, and projections to versioned SQLite rows. | Architecture |
| PA-02 | Prototype assumption | L03 local execution | Queue timing and redrive semantics cannot be demonstrated without infrastructure. | Run the Incremental path synchronously while durably recording every state transition. | Workflow platform |
| PA-03 | Prototype assumption | L06/L07 demo slice | M1 excludes real runtime integration, but a HIGH confidence impact needs two mechanisms. | Add a clearly labeled, complete element-scope runtime fixture that agrees with seeded SCA evidence. | Runtime observation team |
| PA-04 | Prototype assumption | L09/L16 M1 demo | Auto-publish would bypass the requested human review demonstration. | Stamp edges as eligible but require manual review in this M1 prototype. | Governance |
| PA-05 | Prototype assumption | L11 §15 response | The response has one `corroboration` for a multi-edge path but no aggregation rule. | Report the weakest path corroboration using `NONE < DATASET < ELEMENT`, parallel to weakest-band path confidence. | Impact API owner |
| PA-06 | Prototype assumption | L11 §15 | Async full-closure notification protocol, auth, retry, and retention are unspecified. | Implement bounded synchronous depth ≤ 5 only and expose `truncated`; defer `/impact-jobs`. | API and notifications owners |
| PA-07 | Prototype assumption | L01/L03 adapter | The canonical envelope has no platform field although resolver context requires one. | Bind the seeded payments repository to `snowflake` in fixture configuration. | Intake and catalog owners |
| PA-08 | Prototype assumption | Local demo operation | A reproducible demo needs an explicit reset/launch action. | Provide local-only reset and seeded-collection actions; do not carry them into production ingress. | Developer experience |

## Cross-document inconsistencies

| ID | Classification | Source | Impact | Prototype decision | Production owner |
|---|---|---|---|---|---|
| INC-01 | Cross-document inconsistency | L02 §3/§6 says dedupe TTL 14 days; architecture deck Stage 1 and audit slides say 7 days | Replay behavior changes for deliveries between days 8 and 14. | Local demo retains event IDs until reset; production must pin one TTL in versioned intake policy. | L02 owner and architecture |
| INC-02 | Cross-document inconsistency | L09 says auto-publish is active at MVP; L16 M1 explicitly demonstrates a manual proposal and places auto-publish in M2 | Review load and the demonstrated launch path differ. | Follow L16 M1 manual review for this prototype and retain an `autoPublishable` stamp. | Delivery lead and governance |
| INC-03 | Cross-document inconsistency | L03 document control launches Baseline at MVP; L16 schedules the Baseline workflow in M2 breadth | Staffing, exit criteria, and MVP completeness differ. | Implement Incremental only; classify before it to exercise UNKNOWN blocking, but defer Baseline orchestration. | Delivery lead and L03 owner |
| INC-04 | Cross-document inconsistency | L11 FR-002 describes `GET /impact/{urn}?change=`; L11 §15 adds `POST /impact-jobs`; the prototype design uses `POST /api/impact` for bounded sync | Client contracts cannot freeze without selecting method, payload, and job relationship. | Use `POST /api/impact` with the strict checked-in synchronous response schema; reserve `/impact-jobs` for a later contract. | L11 API owner |
| INC-05 | Cross-document inconsistency | Readiness constants pin PRGate ≤ 60 s p95; architecture deck slide 14 speaker notes state a 30-second p95 budget | Capacity and fail-open thresholds differ by 2×. | PRGate is deferred; no latency claim is made. | PRGate product owner and SRE |

## Decisions needed before production implementation

1. Resolve INC-01 through INC-05 and publish a versioned constants/interface table.
2. Supply every CTX value through the named owning team; none should become a code default merely because a fixture exists.
3. Author the six remaining readiness-review lifts called out in the updated verdict: G-L01-1, G-L03-3, G-L04-1, G-L05-1, G-L06-1, and G-L09-2.
4. Specify async impact job authorization, idempotency, callback retries, UI notification, result retention, and relationship to version pins.
5. Decide whether weakest corroboration is the intended path aggregation rule and add it to L11 §15 or revise the response to return per-edge axes.
