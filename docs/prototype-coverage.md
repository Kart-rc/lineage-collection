# Implementation and Evidence Coverage

This is an honest implementation matrix, not a production-readiness declaration. The earlier
prototype labels remain useful: **Implemented**, **Fixture adapter**, **Interface only**, and
**Deferred** describe code breadth. Evidence status is stricter:

- `LOCAL_PASS` — behavior ran locally against its real local adapters and oracle.
- `SYNTH_PASS` — AWS topology/configuration was asserted and synthesized without an AWS call.
- `AWS_REQUIRED` — a live AWS deploy, scale, availability, security-control, canary, or recovery
  result has not run in the required environment.
- `NOT_CONFIGURED` — an enterprise-owned CTX value or integration is intentionally absent.
- `LOCAL_REAL_REPOSITORY_PASS` — an exact external checkout passed a pinned compatibility oracle;
  this is distinct from hermetic fixtures, runtime evidence, and live AWS.

`AWS_REQUIRED` and `NOT_CONFIGURED` never mean `PASS`.

| Component | Implementation status | Current evidence | Included now | Out of prototype scope / remaining external gate |
|---|---|---|---|---|
| L01 URN and resolver | Implemented | `LOCAL_PASS` | Canonical URNs, ordered catalog resolution, aliases, element validation, quarantine, snapshot/version determinants | Enterprise catalog export/schema, platform case/quote corpus and governed alias mutation are `NOT_CONFIGURED` |
| L02 intake and queues | Implemented | `LOCAL_PASS`, `SYNTH_PASS`, live `AWS_REQUIRED` | HMAC intake, dedupe, durable acceptance/outbox, FIFO groups, lease visibility, DLQ/redrive contracts, EventBridge/SQS routing and lane headroom | GitHub/Jenkins canonical receivers and 100/s plus 10k-burst AWS evidence |
| L03 orchestration | Implemented | `LOCAL_PASS`, `SYNTH_PASS`, live `AWS_REQUIRED` | Versioned B1–B10, I1–I10, P1–P8, D1–D6 and N1–N6 definitions; generated ASL parity; immutable handler versions; crash redrive | Live Step Functions execution/canary and production quota evidence |
| L04 SCA engine | Implemented seed pack plus closed Java/Spring cell | `LOCAL_PASS`, Spring Petclinic `LOCAL_REAL_REPOSITORY_PASS`, packaged target `SYNTH_PASS` | Deterministic Python AST matcher; repository-neutral Tree-sitter Java + SQLGlot Spring Boot/Data JPA/PostgreSQL pack; exact citations/transforms/residue; bounded callback Fargate target; exact-revision acquisition for local checkouts and public HTTPS Git origins with pre-connect address revalidation, pinned addresses, no redirects/proxies/credential helpers/hooks, process-group bounds and guaranteed cleanup | Other languages/frameworks, Maven/Gradle module graphs, custom repository bases, dynamic query construction, nonstandard schema discovery, hostile-estate load corpus and enterprise conventions remain outside this cell. Measured against `spring-petclinic-rest` at `698bd832eac0da48e72deb63ce7ab275d98a88fb` (153 paths, 87 production Java files, 1772 facts): framework-sensitive names still do not resolve through on-demand imports (`wildcard-framework-symbol`) because jars are invisible to the analyzer, so an entity whose `@Entity`/`@Table` arrive via `import jakarta.persistence.*;` yields no table mapping; unnamed `CREATE INDEX ON <table> (<col>)` is recorded as `malformed-sql` even though sqlglot parses it, and a `@Table` annotation built from a constant is `dynamic-table-mapping`. Those three force `INTEGRATION_REQUIRED`, which is the designed fail-closed outcome, not a defect |
| L05 LLM gateway | Interface only | `LOCAL_PASS` for skip/no-fabrication contracts; `NOT_CONFIGURED` externally | LLM provenance/confidence contract, residue boundary and no-fabrication behavior | Approved Bedrock gateway, models, prompts, budgets, cache and guardrails |
| L06 runtime observation | Implemented contracts and local plane | `LOCAL_PASS`; live integrations `NOT_CONFIGURED` | Signed expiring non-production sessions, revoke/drain/close manifests, OpenLineage facets, metadata-only SDK mapping, OTel connectivity mapping and artifact binding | Spark/Dask listener installation, OTel collector topology and weekly production hard-deny probe |
| L07 consolidation and confidence | Implemented | `LOCAL_PASS` | Stable edge/provenance identities, commutative/idempotent merge, band/corroboration separation, transform conflict, late runtime reconciliation | Estate-scale partition/hot-key and full contradiction/decay corpus |
| L08 evidence and control stores | Implemented local + AWS adapters | `LOCAL_PASS`, `SYNTH_PASS`, live `AWS_REQUIRED` | Canonical SHA-256 objects, overwrite/tamper refusal, SQLite/Dynamo conditional control, versioned S3 references, production Object Lock/replication topology | Live retention, replication-time and restore evidence with compliance-approved periods |
| L09 proposals and review | Implemented | `LOCAL_PASS` | Deterministic diffs, lifecycle, optimistic lock, immutable approvals/corrections/audit and manual publish | TAS/RBAC ownership, governed auto-publish sampling and calibration corpus are `NOT_CONFIGURED` |
| L10 fenced publication | Implemented local + AWS seams | `LOCAL_PASS`, `SYNTH_PASS`, live `AWS_REQUIRED` | Durable resumable publication operation, immutable manifest, bounded batches, verify, fence, atomic pointer/outbox and failure retain/discard | Live Neptune namespace verification, contention and rebuild drill |
| L11 APIs, UI and PR gate | Implemented | `LOCAL_PASS`, Petclinic product flow `LOCAL_REAL_REPOSITORY_PASS` | FastAPI/product UI, durable collection submit and status (`POST /api/collections`, `GET /api/collections/{commandId}`) shared by FastAPI, the environment-neutral `ProductApi` and the AWS entry point, resilience ledger, accessible graph/evidence, bounded impact, proposal flow and conservative read-only PRGate with freshness recheck | AWS `submit_collection` is `NOT_CONFIGURED` until the Fargate acquisition stage exists; GitHub check credential/install scope and identity-aware enterprise authorization are `NOT_CONFIGURED` |
| L12 security, observability and operations | Implemented local signals; external controls Interface only | `LOCAL_PASS`, `SYNTH_PASS`, live controls `AWS_REQUIRED`/`NOT_CONFIGURED` | Correlation, typed degradation, audit, strict contracts, private/no-NAT CDK, scoped actions, KMS, alarms, paging seam and backups | Org CloudTrail/Config/WAF baseline, paging/severity policy, penetration and live security-negative evidence |
| L13 repository classification | Implemented | `LOCAL_PASS` | Versioned nine-class table, precedence/conflict-to-UNKNOWN, immutable decision record | Estate inventory, monorepo routing UI, override owner/expiry and policy preview |
| L14 infrastructure and deployment | Implementation in progress | `SYNTH_PASS`, package `LOCAL_PASS`, live `AWS_REQUIRED` | Ten CDK stacks, nine Lambda aliases/canaries including explicit classification compute, SCA Fargate, queues/DLQs, DynamoDB/S3/Kinesis/Neptune/API/budget/backup/recovery and exact OCI digests | A2–A9 real stage bindings/product delivery plus clean-account deploy, canary rollback and service-quota validation |
| L15 NFR and resiliency | Implemented local harness | `LOCAL_PASS`; scale/availability/DR `AWS_REQUIRED` | Named failpoints, replay equivalence, publication crash matrix, lane fairness smoke, truthful resilience UI and content-addressed acceptance evidence | 12-hour Baseline, sustained/burst, error-budget, cost, multi-AZ failure and RPO/RTO drills |
| L16 delivery plan | Implemented through Task 21 | Tasks 17–21 `LOCAL_PASS`/`SYNTH_PASS`; Task 22 pending | Build-ready B01–B16 PRDs, deployable packages, executable AWS workflow spine, acceptance harness and operator/diagram handoff | Task 22 PR/review loop and all external evidence above |

## Tasks 17–21 delivery status (Tasks 17–20 implementation plus handoff)

| Task | Result | Commit |
|---|---|---|
| 17 — build-ready PRDs | 16 B-series PRDs with infrastructure, ownership and acceptance rows | `6a23e4a` |
| 18 — deployable units/IaC | Eight Lambda handlers, SCA task, CDK topology and deterministic packages | `b236b9f` |
| 19 — AWS adapters/workflows | Concrete SDK requests, generated four-workflow ASL, exact handler versions and guarded AWS smoke | `831a21b` |
| 20 — acceptance harness | Named faults, local load/fairness smoke and content-addressed manifests | `5c28569` |
| 21 — operator/architecture handoff | Commands, trigger/mapping tables, evidence coverage, resolved ambiguities and six normative Mermaid views | Current documentation commit |

## Current gate summary

| Gate | Outcome | Meaning |
|---|---|---|
| Backend domain/API/adapter/acceptance tests | `LOCAL_PASS` | Local correctness and fault oracles ran |
| React tests and production build | `LOCAL_PASS` | Product surfaces and accessibility contracts ran |
| CDK assertions, fixture synth and explicit production synth | `SYNTH_PASS` | Templates are coherent and fail closed; no cloud resource was created |
| Lambda/SCA OCI package build | `LOCAL_PASS` | Pinned multi-architecture images and metadata were produced locally |
| Local acceptance smoke | 3 `PASS`, 5 `AWS_REQUIRED` | Replay/fairness/evidence passed; AWS-only NFRs stayed external |
| Exact Spring Petclinic checkout | `LOCAL_REAL_REPOSITORY_PASS` | Exact revision `88e37c15…` produced 15 static edges, complete 131-path disposition, deterministic evidence and duplicate no-effect |
| Ephemeral AWS deploy/smoke | `AWS_REQUIRED` | No approved profile/account/opt-in was present |
| Production canary, scale, security and DR | `AWS_REQUIRED` | Must run in the specified environment and cadence |
| Enterprise catalog/GitHub/Bedrock/runtime/identity integrations | `NOT_CONFIGURED` | CTX owners must provide approved contracts/values |

## Java/Spring compatibility boundary and retained proof

The v1 Java cell is repository-neutral production logic. It supports a root literal Maven and/or
Gradle Spring Boot/Data JPA cell, production Java under `src/main/java`, Spring Data repositories
based on the closed supported interfaces, JPA entity/table annotations, cited repository calls, and
one exact `src/main/resources/db/<profile>/schema.sql`. It never runs repository code. Petclinic
names and expected counts exist only in the opt-in integration oracle and documentation.

The retained local-real proof is written to
`data/acceptance/<run-id>/java-spring/sha256-<checksum>.json` (Git-ignored). At the pinned official
Petclinic revision it records six entity mappings, three repository→entity→table chains, five caller
components, 15 exact static edges (10 reads, 5 writes), eight explicitly retained non-edge schema
residue records, zero unresolved invocations, and 131 tracked paths partitioned into 33 completed
and 98 skipped paths. Its bounded relational effect proof uses one read-only SQLite transaction and
dynamically covers all 33 current user tables, complete table/index/view/trigger schema records,
column/foreign-key/index metadata and every typed value. Exact physical bytes before/after replay
catch same-count updates as well as row and schema changes. Only documented time/lease fields and
explicitly listed time-derived references are normalized in the retained logical digest. Two fresh-
state runs produce the same manifest/SCA/logical checksums. The hardened manifest checksum is
`sha256:84d6345e2d597aef0f068d6a6c3b24f4bf0acaf438b24311066d8fda8b304f40`.

The dedicated proof clears inherited variables and invokes the preprovisioned locked virtual
environment's Python directly with isolated mode; it does not invoke a package manager or network.
Its standard-library supervisor allowlists child variables, caps both streams, enforces a global
timeout/process-group kill, and accepts only the exact PASS schema. A missing or unsafe environment
is `INTEGRATION_REQUIRED`, not an invitation to install or sync.

Compatibility gaps remain explicit: arbitrary Gradle/Maven execution or dynamic dependency logic,
multi-module build graphs, nonstandard source/schema roots, Hibernate/native-query extensions not in
the closed rule set, dynamic queries, custom repository frameworks, Kotlin, reactive/R2DBC, runtime
execution truth, and enterprise catalog conventions. Those inputs return residue,
`INTEGRATION_REQUIRED`, or require another versioned analyzer cell; they are not silently inferred.
This proof leaves runtime `NOT_PROVIDED`, production collection off, and all live AWS evidence
`AWS_REQUIRED`.

## End-to-end local mapping

```text
Signed push or exact bounded Git checkout (L02)
  → durable command + classification (L03/L13)
  → selected Python or Java/Spring static evidence + pinned catalog/schema URNs (L04/L01)
  → optional exact-artifact runtime join (L06)
  → immutable evidence and complete coverage (L08/L15)
  → confidence merge and proposal (L07/L09)
  → manual approval + resumable fenced publication (L10)
  → exact deployment promotion / read-only PRGate (L03/L11)
  → lineage, impact and resilience product surfaces (L11/L12)
```

The repository proves the architecture's hard local correctness properties and that the AWS shape
can be synthesized and packaged. It deliberately does not convert unexecuted AWS, enterprise,
scale, availability or recovery work into a passing claim.
