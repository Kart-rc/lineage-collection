# Lineage Platform Executable Acceptance Specification

**Version:** 1.0.0

**Status:** Normative

**Architecture source:** `docs/plans/2026-08-05-lineage-collection-architecture-refactor-design.md`

## 1. Purpose and authority

This document is the executable acceptance index for lineage collection. It replaces unresolved
references to an external or missing `Test Suite` with stable build-unit acceptance IDs, exact
evidence requirements, cadences, and release consequences.

Domain requirements remain authoritative in L01-L16. Build PRDs will own the detailed scenario rows.
This index defines how those rows are identified, executed, evidenced, and used as delivery gates.

An assertion is not an accepted platform property until its required test or drill has produced a
schema-valid `AcceptanceEvidenceManifest`. A local substitute cannot prove an AWS-only availability,
scale, multi-AZ, security-control, or disaster-recovery claim.

## 2. Build-unit acceptance ownership

| Build unit | Acceptance scope |
|---|---|
| B01 — contracts and correlation | Schema strictness, compatibility, generated models, correlation propagation |
| B02 — catalog snapshot and resolver | Golden corpus, snapshot pinning, last-good behavior, cross-language equality, latency |
| B03 — evidence and control stores | Immutability, checksums, retention, conditional state, rebuild, RPO/RTO |
| B04 — event intake and lane router | Authentication, durable ack, dedupe, ordering, fairness, replay, burst absorption |
| B05 — classifier and orchestrator | Trigger policy, every workflow state/branch, timeouts, redrive, terminal behavior |
| B06 — SCA worker and rule packs | Parser/rule fixtures, negatives, determinism, residue, hostile repository bounds |
| B07 — LLM inference gateway | Tiering, structured output, guardrails, cache, budget, skip-with-record, rollout |
| B08 — runtime session and ingestion | Grant/expiry/revoke, production deny, metadata-only validation, drain reconciliation |
| B09 — runtime emitters and integrations | OpenLineage/OTel/SDK compatibility, artifact binding, overhead, completeness |
| B10 — consolidation and confidence | Idempotency, commutativity, band matrix, conflict, decay, rename, partition behavior |
| B11 — proposal and review | Lifecycle, concurrent decision, correction, audit sampling, narrowing, corpus |
| B12 — publisher and projections | Fence, stage/verify/swap, crash recovery, rollback, rebuild, pointer integrity |
| B13 — query, impact and PRGate | Bounded traversal, version pins, verdict policy, freshness, deadline, read-only proof |
| B14 — review and operations UI | Authorization, accessibility, state fidelity, degradation and staleness rendering |
| B15 — operations and recovery automation | Correlation, alarms/runbooks, error budgets, degradation and recovery evidence |
| B16 — platform IaC and delivery | CDK assertions, clean deploy, least privilege, canary, rollback, warm standby |

## 3. Acceptance-row contract

Every detailed acceptance row uses a stable `Bxx-AC-nnn` identifier and contains:

| Field | Required content |
|---|---|
| Acceptance ID | Stable identifier such as `B04-AC-001` |
| Requirement links | Owning FR, NFR, security, failure, infrastructure, or architecture invariant IDs |
| Scenario | Given/When/Then including initial durable state and exact input |
| Test level | Unit/property, schema/contract, component, integration, E2E, load, fault, security, or DR |
| Fixture and oracle | Versioned fixture plus exact state, output, checksum, error code, or invariant |
| Environment | Hermetic local, AWS ephemeral, staging, controlled production probe, or DR environment |
| Infrastructure assertions | Required resources, policies, encryption, alarms, rollback, and cost controls |
| Threshold | Count, percentile/window, deadline, loss/divergence rule, or expected error |
| Fault point | Named boundary and required retry, redrive, rollback, or degradation behavior |
| Evidence | Machine-readable report, metrics query, checksum, logs, and immutable references |
| Cadence and owner | When it runs and who owns remediation |
| Release consequence | Merge/deploy/GA block, error-budget debit, quarantine, or tracked defect |

Acceptance rows may cover multiple requirements. Every P0 requirement, named failure, security rule,
and infrastructure invariant must have at least one row. A row without an implemented test ID and
fixture is `SPECIFIED`, not `PASS`.

## 4. Test levels

1. **Unit and property:** identity, normalization, ordering, idempotency, commutativity, state
   transitions, coverage arithmetic, monotonic fences, and bounded policy decisions.
2. **Schema and contract:** strict JSON Schema, producer/consumer compatibility, version rejection,
   provider fixtures, OpenLineage facets, OTel mappings, and SDK envelopes.
3. **Component:** a real component with local adapters and deterministic clocks/fault points.
4. **Integration:** local cross-component flow or AWS ephemeral adapters and resources.
5. **End to end:** exact source/artifact through evidence, proposal, publication, and query/check.
6. **Load and fairness:** sustained rate, burst, backlog drain, lane isolation, latency, and cost unit.
7. **Fault and chaos:** crash after a named boundary, dependency degradation, retry storm, contention,
   projection loss, queue redrive, and corruption.
8. **Security and privacy:** invalid auth, least-privilege negative, production deny, prohibited data,
   tenant isolation, secret/log redaction, and auditability.
9. **Disaster recovery:** restore/reconcile control state, writer election, projection rebuild,
   pointer verification, measured RPO, and measured RTO.

## 5. Historical Test Suite mapping

The source PRDs retain historical section labels. They map to this repository as follows:

| Historical reference | Normative repository target |
|---|---|
| Test Suite §2 — seeded repositories | Versioned fixtures, golden corpora, and expected-lineage oracles owned by B02, B06, B09, and B10 |
| Test Suite §3 — unit suites | Build-unit unit/property/component rows and their tests |
| Test Suite §4 — integrated scenarios | Baseline, Incremental, PRGate, Deployment, replay/rebuild, and runtime E2E rows |
| Test Suite §5 — contracts | `packages/contracts`, compatibility fixtures, and B01 contract rows |
| Test Suite §6 — live canaries | Controlled staging/production probes owned by B02, B07, B08, B13, and B15 |
| Test Suite §7 — cadence and chaos | The release cadence and degradation/DR gates in sections 7 and 8 below |

Historical labels are navigation aliases, not evidence. A build must link the concrete acceptance ID
and evidence manifest.

## 6. Initial acceptance index

These rows are the minimum spine. Build PRDs may add rows but may not weaken them.

| Acceptance ID | Minimum passing scenario | Required evidence class |
|---|---|---|
| B01-AC-001 | Old/new producer-consumer fixtures validate; unknown incompatible schema is rejected with a typed error | PR contract report |
| B02-AC-001 | Identical input and pinned snapshot produce byte-identical resolved/quarantined output; last-good is explicit when refresh fails | Golden-corpus report |
| B03-AC-001 | Overwrite/corruption is refused; a clean projection rebuilt from immutable manifests has zero checksum divergence | Fault/rebuild report |
| B04-AC-001 | Signed intake durably accepts within 500 ms p95 at 100/s; a 10,000-event burst has zero loss and no duplicate effect | AWS load report |
| B05-AC-001 | Killing after every stage side effect and redriving converges to the same manifests and terminal state | Fault matrix |
| B06-AC-001 | Every supported parser/rule-pack cell has positive and negative fixtures; replay is byte-identical; hostile inputs terminate within bounds | Parser corpus report |
| B06-AC-002 | The exact official Spring Petclinic revision produces the complete closed Java/Spring/PostgreSQL oracle through durable collection and duplicate replay | Local real-repository manifest |
| B07-AC-001 | Every guardrail rejects its adversarial fixture; budget exhaustion and gateway outage never fabricate an edge | Contract/fault report |
| B08-AC-001 | Expired, revoked, malformed, prohibited-data, artifact-mismatched, and production observations write no evidence | Security report |
| B09-AC-001 | OpenLineage, OTel, and SDK fixtures retain distinct mechanisms/granularity and emit a reconciled closing manifest within overhead target | Integration report |
| B10-AC-001 | Duplicate and permuted assertions converge to one edge version with the exact band/conflict/corroboration result | Property report |
| B11-AC-001 | Concurrent review has one winner; correction creates immutable successors; audit sample/narrowing is reproducible | Lifecycle report |
| B12-AC-001 | A stale publisher can never activate; crash redrive yields exactly one active verified namespace; rebuild divergence is zero | Property/fault report |
| B13-AC-002 | The exact pinned Petclinic revision submitted through `POST /api/collections` reaches a terminal `IN_REVIEW` run and proposal with the 15/10/5 oracle, the complete 131-path disposition, `NOT_PROVIDED` runtime status, and a duplicate submission that leaves the database and evidence digests byte-identical | Collection product-flow report |
| B13-AC-001 | PRGate rechecks head/environment, writes no lineage state, and produces explicit `WARN` by 120 seconds when incomplete/degraded | E2E fault report |
| B14-AC-001 | UI exposes stale/incomplete/out-of-sync states accessibly and renders only server-authoritative transitions | UI accessibility report |
| B15-AC-001 | Every alarm has a tested runbook; each degradation row produces its promised visible state without corrupting truth | Operations drill report |
| B16-AC-001 | CDK assertions, clean-account deployment, canary/rollback, and warm-standby drill satisfy approved infrastructure and recovery policy | Deploy/DR report |

## 7. Release cadence

### Every PR

Run affected unit/property, schema/contract, deterministic replay, local adapter, CDK assertion,
security/static, and hermetic boundary-fault tests. Failure blocks merge.

### Every deployment

Run exact-artifact staging smoke, additive migration compatibility, canary, security negatives,
rollback, and pointer/package/deployed-digest invariants. Failure stops or rolls back promotion.

### Nightly

Run sustained/burst intake, duplicate replay, backlog drain, interactive-under-bulk, differential
Baseline/Incremental corpus, projection rebuild sample, and cache/manifest reconciliation.

### Weekly

Run publication-fence contention, production runtime hard-deny, scoped DLQ/redrive, and selected live
dependency canaries.

### Monthly

Rotate through every dependency degradation row and reconcile error-budget and unit-cost telemetry.

### Quarterly

Run the full warm-standby recovery: restore/reconcile control state, elect one writer, rebuild
projections, verify active pointers, and measure approved RPO/RTO.

## 8. Environment and claim policy

| Evidence status | Meaning |
|---|---|
| `PASS` | The required test ran in the required environment and met every threshold |
| `FAIL` | The test ran and at least one oracle or threshold failed |
| `WAIVED` | An approved, owned, expiring waiver and compensating control exist |
| `AWS_REQUIRED` | Local behavior is verified, but an AWS-only scale, security, availability, AZ, or DR claim has not run |
| `NOT_CONFIGURED` | An enterprise CTX value or integration is not supplied; the seam and fixture may still pass |
| `HERMITIC_LOCAL_PASS` | The deterministic fixture/local-adapter suite passed without depending on an external checkout |
| `LOCAL_REAL_REPOSITORY_PASS` | A separately supplied exact real checkout passed its pinned compatibility oracle; this is not runtime or AWS evidence |
| `LOCAL_REAL_REPOSITORY_REQUIRED` | The opt-in exact checkout was not supplied, so no real-repository pass is claimed |
| `RUNTIME_NOT_PROVIDED` | Static lineage ran without a validated runtime observation and no runtime corroboration is claimed |

`AWS_REQUIRED`, `NOT_CONFIGURED`, `LOCAL_REAL_REPOSITORY_REQUIRED`, and
`RUNTIME_NOT_PROVIDED` are never rendered or counted as `PASS`.

### 8.1 Local real-repository compatibility proof

`B06-AC-002` is an opt-in proof over the official Spring Petclinic repository at exact revision
`88e37c15cf6fc8490b01bc3e8e2c800cec1ac272`. The repository is an acceptance oracle only; the
production analyzer registry and Java/Spring pack contain no Petclinic branch or identifier.

```bash
git clone https://github.com/spring-projects/spring-petclinic.git /tmp/spring-petclinic
git -C /tmp/spring-petclinic checkout --detach 88e37c15cf6fc8490b01bc3e8e2c800cec1ac272
LINEAGE_REAL_REPOSITORY_CHECKOUT=/tmp/spring-petclinic \
  ./scripts/run_real_repository_acceptance.sh
```

The dedicated runner validates the checkout with the bounded exact-Git adapter and does not run
Maven, Gradle, tests, application code, hooks, or repository executables. In each of two fresh local
state directories it drives the actual `collect-checkout` durable flow; it also repeats the command
in the same state and requires `DUPLICATE` with unchanged command, run, proposal, coverage and
database state. The proof opens SQLite explicitly read-only, enables `query_only`, and holds one
transaction across all reads. Under explicit schema/table/row/byte/time/busy bounds it records every
table/index/view/trigger row from `sqlite_schema` (including automatic indexes), `table_xinfo`,
foreign keys, index metadata, and every typed row value. Complete physical snapshots catch in-place
updates, inserts, deletes, and schema changes; row-count equality alone is not the oracle. The
retained logical digest normalizes only documented timestamp/lease fields (including JSON fields)
and their explicitly listed time-derived evidence references so two fresh runs remain comparable.

The runner clears inherited variables and invokes the already provisioned locked virtual
environment's Python directly in isolated mode. Its standard-library supervisor uses an explicit
secret-free child allowlist, bounded streams, a global timeout/process-group kill, and strict full
PASS JSON validation. No package manager or network operation is invoked. An absent or unsafe
runner/environment/dependency produces bounded `INTEGRATION_REQUIRED` instead of attempting
installation. The exact oracle is 15 static edges (10 `READS`, 5 `WRITES`), zero unresolved
invocations, full 131-path disposition (33 completed, 98 skipped, zero unsupported/failed), and an
`IN_REVIEW` proposal. Runtime remains `NOT_PROVIDED`, production collection is off, and AWS remains
`AWS_REQUIRED`.

On success the runner publishes one canonical, write-once manifest using trusted-root directory
descriptors, no-follow traversal, a synced temporary/file/directory sequence, and post-publication
directory identity validation to
`data/acceptance/<run-id>/java-spring/sha256-<checksum>.json`. It contains only source determinants,
digests, durable identities, coverage/count summaries and the expected entity/repository/call-site
oracle—never checkout paths, source contents, raw queries, timestamps, credentials, or copied
external source. The two fresh runs must produce byte-identical manifest and SCA evidence checksums.
Missing/invalid checkout, wrong origin/revision, tamper, content-address conflict, incomplete scope,
or oracle drift exits nonzero and cannot produce `LOCAL_REAL_REPOSITORY_PASS`.

The hardened pinned proof checksum is
`sha256:84d6345e2d597aef0f068d6a6c3b24f4bf0acaf438b24311066d8fda8b304f40`;
its retained database logical digest is
`sha256:5782d1b552114938be2b106cf74df9cf912c450e0fc2c9f48a12e33322173452`.

The default hermetic acceptance command remains independent of the external checkout:

```bash
make acceptance-smoke
```

Without `LINEAGE_REAL_REPOSITORY_CHECKOUT`, it reports a separate
`LOCAL_REAL_REPOSITORY_REQUIRED` row while retaining the hermetic result. With the variable set it
runs both evidence classes. The executable mapping is:

| Claim | Command | Retained evidence |
|---|---|---|
| Hermetic local correctness | `make acceptance-smoke` | Schema-valid manifests under `data/acceptance/<run-id>/B*/` |
| Real Java/Spring compatibility | `LINEAGE_REAL_REPOSITORY_CHECKOUT=<exact-checkout> ./scripts/run_real_repository_acceptance.sh` | `data/acceptance/<run-id>/java-spring/sha256-<checksum>.json` |
| Runtime corroboration | Separate approved runtime-session integration | `RUNTIME_NOT_PROVIDED` in this proof |
| AWS topology/behavior | `make synth` / explicit ephemeral AWS workflow | `AWS_REQUIRED` until the required live environment runs |

## 9. AcceptanceEvidenceManifest

Every runner emits a record conforming to
`packages/contracts/acceptance-evidence-manifest.schema.json`. At minimum it names the build unit,
acceptance ID, exact artifact digest, outcome, and one or more immutable evidence references.

Detailed build PRDs extend this core with timestamps, environment, test command, metrics, thresholds,
fault point, owner, waiver, and correlation fields. Extensions require a versioned schema revision;
unknown fields are rejected rather than silently discarded.

## 10. Readiness rules

A build unit is `READY` only when:

- its boundary and input/output/error schemas are versioned;
- every P0 rule and named failure has an acceptance row, implemented test, fixture, and oracle;
- every infrastructure resource has a CDK assertion, least-privilege negative, alarm/runbook, cost
  owner, recovery behavior, and local contract equivalent where applicable;
- every external CTX seam has schema validation, fixture behavior, and fail-closed or explicit
  fail-degraded semantics;
- required evidence is current for its cadence; and
- no P0 waiver is implicit or expired.
