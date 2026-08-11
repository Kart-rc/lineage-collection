# Lineage Platform Remaining Goals

| Field | Value |
|---|---|
| Status | Active |
| Branch | `feat/remaining-goals` (on top of merged `codex/lineage-prototype`) |
| Pull request | [#2 — Build durable lineage collection and Spring lineage proof](https://github.com/Kart-rc/lineage-collection/pull/2) |
| Last reconciled | 2026-08-11 |

## 1. Goal

Deliver a production-shaped lineage collection platform that can safely acquire an exact repository
revision, combine static and runtime evidence through durable orchestration, produce a complete and
reviewable lineage proposal, publish an approved versioned graph, expose the result through the
product API and React application, and prove the deployment in AWS.

The goal is complete only after the implementation, evidence, target architecture, live AWS proof,
and pull-request review gates below are all satisfied and the reviewed branch is merged to `main`.
Local correctness, generated infrastructure, or a draft pull request alone cannot satisfy the goal.

## 2. Status language

| Status | Meaning |
|---|---|
| `COMPLETE` | Implemented, freshly verified at its required evidence level, reviewed, and committed. |
| `IN_PROGRESS` | A bounded implementation slice exists, but required behavior or review remains. |
| `READY` | Requirements and dependencies are known; implementation has not started. |
| `EXTERNAL_REQUIRED` | Code may exist, but completion requires an approved external environment or enterprise-owned value. |
| `BLOCKED` | Progress cannot continue without a named decision, permission, or unavailable dependency. |

Evidence labels in [the implementation coverage matrix](prototype-coverage.md) remain normative:
`LOCAL_PASS`, `SYNTH_PASS`, `LOCAL_REAL_REPOSITORY_PASS`, `AWS_REQUIRED`, and `NOT_CONFIGURED` must
not be conflated.

## 3. Current baseline

The following capabilities are already available and are not remaining goals unless a later change
regresses them:

- explicit domain/application ports, immutable models, durable commands, leases, stage idempotency,
  transactional outbox delivery, lane-aware queues, coverage manifests, retries, and fault recovery;
- Baseline, Incremental, PRGate, Deployment, NightlyReconciliation, runtime reconciliation, review,
  and resumable fenced-publication workflows;
- local control/evidence/query implementations and AWS DynamoDB, S3, SQS, Kinesis, Lambda, Fargate,
  Step Functions, Neptune, API Gateway, CloudFront, and operational adapter packages;
- versioned contracts and build-ready PRDs for intake, orchestration, SCA, runtime evidence,
  consolidation, review, publication, product surfaces, operations, and infrastructure;
- Python SDK, OTel, and OpenLineage runtime paths with metadata-only policy, leases, windows,
  revocation, kill-switch, reconciliation, and production hard-deny contracts;
- deterministic Python analysis and the generic Tree-sitter Java/Spring Data JPA/PostgreSQL cell;
- exact local Git checkout validation and the pinned Spring Petclinic proof at revision
  `88e37c15cf6fc8490b01bc3e8e2c800cec1ac272`;
- Petclinic result: 15 static edges, 10 reads, 5 writes, zero unresolved invocations, and complete
  disposition of 131 tracked paths as 33 completed and 98 skipped;
- duplicate repository collection returning the same durable identities with no database, evidence,
  source-read, or analyzer side effects;
- hardened retained-evidence execution with bounded processes, consistent SQLite snapshots, complete
  schema/data effect proof, and symlink/TOCTOU-safe publication;
- repository acquisition request policy for development-only local checkouts and canonical public
  HTTPS Git origins with exact commits, plus bounded public DNS-address validation;
- the existing product API, Operations, Runs, Review, Lineage Explorer, impact, resilience, runtime
  administration, and deployment surfaces for already-supported flows;
- AWS packaging, workflow-definition parity, CDK assertions, and synthesis evidence.

The most recent retained real-repository evidence checksum is
`sha256:84d6345e2d597aef0f068d6a6c3b24f4bf0acaf438b24311066d8fda8b304f40`.

## 4. Remaining goals

Every remaining item below is blocked on an owner decision or a human step. Nothing further can be
implemented autonomously. In the order that unblocks the most:

| # | Needed from the owner | Unblocks |
|---|---|---|
| 1 | Approve one outbound fetch of the pinned public Petclinic commit (`LINEAGE_REAL_REMOTE_ACQUISITION=1`, `tests/integration/test_remote_repository_acquisition.py`) | G2 → `COMPLETE` |
| 2 | Sign off the architecture review (both the offline HTML and the Mermaid block have been rendered and inspected) | G1 → `COMPLETE` |
| 3 | Sign off the product experience (the browser smoke has been run and passes) | G4, G5 → `COMPLETE` |
| 4 | Approve an AWS account, profile, region, context values, and opt in to billable/externally visible traffic | G6, and the deferred AWS `submit_collection` in G3 |
| 5 | Decide the branch and pull request: work sits on `feat/remaining-goals`, nothing is pushed, and PR #2 named above is already merged | G7 |


### G1 — Publish the canonical target AWS architecture

**Status:** `IN_PROGRESS` — artifacts, assertions and visual inspection complete; owner review remains

Delivered on branch `feat/remaining-goals` (`f205d39`):

- `docs/architecture/lineage-platform-target.md` — normative Mermaid, status legend, arrow legend,
  layer responsibilities, evidence boundaries, plan reconciliation, and known target gaps;
- `docs/architecture/lineage-platform-target.html` — offline, script-free, self-contained rendering
  generated by `scripts/render_target_architecture.py` (`make architecture` / `make architecture-check`);
- eight executable assertions in `tests/test_documentation.py` covering required layers, the status
  legend and its evidence labels, every required production component, exclusion of local mappings,
  labelled arrows with durable-transport asynchrony, the review/fencing/coverage/exact-artifact
  invariants, the evidence boundaries, and byte-exact Mermaid-to-HTML parity.

The offline HTML was rendered and inspected in headless Chrome with no network access: the
evidence-boundary banner, the four-class legend, the component tally (24 verified, 19 synthesized,
9 partial, 7 planned = 59), all eight layers with correct status colouring, the double-bordered
human-review gate, the arrow legend, and the full 88-row edge table with its synchronous/durable
column all render correctly.

That inspection found and fixed real staleness: repository acquisition and the React product
application were still `partial` after G2 and G4 landed.

The normative Mermaid block was also rendered (Mermaid 11, headless Chrome): it parses with no
errors and produces exactly 59 nodes and 88 edges, matching both the parser and the HTML rendering,
with the status colouring and the hexagonal review gate intact.

One honest observation from that render: with eight subgraphs and 88 edges, the `flowchart LR`
auto-layout is correct but dense, and long edge routing makes it hard to read as a presentation
graphic. The generated HTML companion — layers plus an explicit edge table — is the better artifact
for a human reader; the Mermaid block remains normative as the machine-checkable source.

Remaining before `COMPLETE`: owner sign-off on the target-only architecture review.

Create one authoritative architecture view that describes the production AWS target only. It must
not show SQLite, local files, Vite, in-process workers, or other local mappings as architecture
components.

**Required artifacts**

- `docs/architecture/lineage-platform-target.md` containing the normative Mermaid source;
- `docs/architecture/lineage-platform-target.html` containing an offline browser-ready rendering;
- documentation assertions that prevent omission of required layers, mappings, status legend, and
  evidence boundaries.

**Required content**

- GitHub, Jenkins, TAS, schedules, deployment signals, and runtime/test observations;
- API Gateway/Lambda normalization, EventBridge, interactive/events/bulk SQS lanes with DLQs, and
  the Kinesis runtime-evidence lane;
- DynamoDB durable commands, dedupe, leases, stages, coverage, outbox, and fencing state;
- Baseline, Incremental, PRGate, NightlyReconciliation, deployment promotion, and runtime-session
  orchestration;
- repository acquisition, classification, deterministic SCA, Java/Spring, SQL, Python, OTel,
  OpenLineage, Spark/Dask, and bounded residue handling;
- immutable S3 evidence, consolidation/confidence, proposal/review, fenced publication, Neptune,
  OpenSearch where applicable, product/query APIs, CloudFront/S3 SPA, and Cognito/OIDC;
- IAM, KMS, AppConfig, CloudWatch, X-Ray, CloudTrail, retry, DLQ, replay, backup, and recovery seams;
- truthful status styling for implemented and verified, implemented and synthesized, partially wired,
  and planned/deferred components.

**Acceptance criteria**

- Mermaid and HTML communicate the same target topology and status.
- Every arrow has a clear artifact or event meaning; no line implies a synchronous dependency where
  the implementation is durable and asynchronous.
- Human review, exact-artifact deployment, runtime non-blocking behavior, coverage completeness, and
  fenced publication are visually explicit.
- The diagram reconciles the architecture, runtime, AWS completion, build PRD, and product plans
  linked in section 8.

### G2 — Complete safe remote Git exact-revision acquisition

**Status:** `IN_PROGRESS` — implementation complete; the opted-in GitHub run remains

Delivered on branch `feat/remaining-goals` (`75538d5`). Every item below is implemented in
`apps/api/src/lineage_api/infrastructure/remote_git_source.py` and wired from
`apps/api/src/lineage_api/dependencies.py`:

- destinations are resolved and revalidated inside `acquire`, immediately before the connection, so a
  request-shape check cannot be rebound to a private address at connect time;
- resolved public addresses are pinned through `http.curloptResolve`; `http.followRedirects=false`,
  `http.proxy=`, and a per-origin proxy override close redirects and ambient proxies;
- the trusted administrator-owned absolute Git executable runs with `--template=`, `core.hooksPath`,
  empty `credential.helper` and `core.askPass`, `protocol.allow=never` plus `protocol.https.allow`,
  no shell, and a from-scratch environment allowlist that inherits no secrets or proxy variables;
- `BoundedProcessGroupRunner` streams and caps stdout/stderr, enforces an overall deadline across all
  Git invocations, and terminates the complete process group through TERM, bounded group probing,
  and KILL escalation — proven against a `SIGTERM`-ignoring forked descendant;
- only the requested exact 40-character commit is fetched (`--depth 1 --no-tags`) into a `0700`
  private temporary checkout;
- origin, exact `HEAD`, clean tracked state, path/blob/count limits, and symlink or submodule
  conditions are verified by the hardened `LocalGitRepositorySource` reader;
- the snapshot is materialized in memory before the checkout is removed in a `finally` block on
  success and on every failure path;
- both providers are wired to `RepositoryAcquisitionService` and `RepositoryCollectionService` from
  the composition root, and a test asserts the application layer imports no concrete infrastructure.

Evidence: 32 offline tests in `apps/api/tests/infrastructure/test_remote_git_acquisition.py`
(hostile environment, credentials, hooks, proxies, redirects, DNS, output flood, timeout, orphan
cleanup, `SIGTERM`-resistant descendants, real exact checkout through an injected transport) and 5
in `apps/api/tests/test_repository_source_wiring.py`.

Remaining before `COMPLETE`: run the opt-in GitHub acquisition in
`tests/integration/test_remote_repository_acquisition.py` with
`LINEAGE_REAL_REMOTE_ACQUISITION=1`. It is skipped by default because it is externally visible
traffic and requires explicit opt-in under section 7.

**Acceptance criteria**

- hostile environment, credential, hook, proxy, redirect, DNS, output-flood, timeout, orphan, and
  `SIGTERM`-resistant descendant tests fail closed with bounded non-echoing errors;
- offline tests exercise a real exact checkout through an injected, legitimate transport boundary;
- an opted-in GitHub run acquires the pinned Petclinic commit and reproduces the existing immutable
  snapshot and lineage oracle;
- repeated acquisition and collection reuse durable identities and produce no new effects.

### G3 — Expose durable collection submit and status APIs

**Status:** `IN_PROGRESS` — local and contract surfaces complete; AWS submit deferred to G6

Delivered on branch `feat/remaining-goals`:

- `apps/api/src/lineage_api/application/collections.py` — one environment-neutral
  `parse_collection_submission` plus `CollectionService` and a `CollectionStore` port. FastAPI, the
  neutral `ProductApiService`, and the AWS entry point all validate through it, so no surface can
  accept a request another would reject.
- schema v9 `repository_collections` plus `infrastructure/sqlite_collections.py` — the durable
  status projection keyed by durable command, the local analogue of the DynamoDB ledger item.
- `POST /api/collections` returns `202` with `Location` for accepted, duplicate, and reused
  submissions; `GET /api/collections/{commandId}` returns the same stable document, including a
  `terminal` flag so a client polls only while the command is non-terminal.
- Typed FastAPI models forbid unknown fields and mutable revisions; `LOCAL_CHECKOUT` is refused with
  `LOCAL_SOURCE_DISABLED` unless the development policy is explicitly enabled; responses carry
  stable codes and correlation IDs and never contain checkout paths, credentials, raw Git output, or
  stack traces.
- `AwsProductQueryProjection.get_collection` reads the ledger projection consistently.

**Remaining before `COMPLETE`:** `AwsProductQueryProjection.submit_collection` fails closed with
`501 COLLECTION_SUBMIT_NOT_CONFIGURED`. In AWS, repository acquisition runs as a Fargate stage, so an
honest submit enqueues a durable command and returns a non-terminal `QUEUED` status. That acquisition
stage is G6 work; synthesizing a local-shaped synchronous submit would be the AWS-only semantic fork
that section 5 forbids. The refusal is asserted by a test and never touches AWS.

The original requirement follows.

Add the product contract that turns repository submission into a durable, queryable collection
resource.

**Required API**

- `POST /api/collections` accepting discriminated `LOCAL_CHECKOUT` and `GIT` sources;
- `GET /api/collections/{commandId}` returning durable state, stages, coverage, lineage counts,
  bounded failure, run identity, and proposal identity;
- HTTP `202` plus `Location` for accepted, duplicate, and reused submissions;
- local-source mode available only under the explicit development policy;
- contract parity across FastAPI, environment-neutral `ProductApi`, and the AWS API Gateway/Lambda
  entry point.

**Acceptance criteria**

- strict typed models forbid unknown fields, overlong text, mutable revisions, incompatible analyzer
  selections, unsafe source modes, and malformed URLs;
- equivalent requests return the same collection, command, run, and proposal identities;
- unknown commands and terminal failures use stable status/error codes and correlation IDs;
- responses never expose checkout paths, URL credentials, raw Git output, source content, secrets,
  or stack traces;
- API, product-contract, and AWS entry-point tests cover both source modes and failure classes.

### G4 — Replace the placeholder with the repository collection product flow

**Status:** `IN_PROGRESS` — implemented, covered and browser-smoked; owner sign-off remains

Delivered on branch `feat/remaining-goals` (`bb1198c`):

- typed `submitCollection` / `collection` client methods with defensive normalization that drops
  non-conforming members rather than rendering them;
- `RepositoryCollectionForm` with a source-mode switch, exact-revision validation, accessible
  labels, single submit while pending, bounded error rendering with code and correlation ID, and
  retained input after a recoverable failure;
- `CollectionStatus` rendering the stage timeline, coverage counts, edge/read/write/residue/
  unresolved counts and run/proposal links, polling only while `terminal` is false and invalidating
  the overview, runs and proposals queries exactly once per finished collection;
- Operations now leads with repository collection; the seeded demo is a labelled secondary action
  shown only under the development policy;
- production rendering omits local-checkout mode rather than disabling it, and a checkout path is
  never serialized for a `GIT` source, so a stale form value cannot reach the wire.

Evidence: 11 new component tests plus 2 client tests; the full web suite is 28 passing and the
TypeScript build is clean.

A headless-Chrome smoke against the built app (API on `:8000`, `vite preview` on `:5199`) verified:
every form control carries an associated label; both source modes are offered under the development
policy; the submit control is keyboard focusable; a non-exact revision is refused with an accessible
alert and the typed values survive; a real submission renders the stage chips, the count grid and
both navigation links; and the console is free of errors throughout.

The smoke found a defect the unit tests had enshrined: the status panel linked to `/proposals/{id}`,
which is not a route and fell through to the Operations page. The proposal detail route is
`/review/{id}` (`85795cc`).

Remaining before `COMPLETE`: owner sign-off on the rendered experience.

Make real repository collection the primary Operations workflow while retaining the seeded demo only
as a clearly labelled secondary development action.

**Required product behavior**

- typed client methods for collection submission and status;
- source-mode form for local development checkout or Git URL plus exact commit;
- repository, environment, system, analyzer pack, ruleset, and schema-profile fields;
- production rendering that omits local-path mode rather than merely disabling it;
- polling only while the durable command is non-terminal;
- stage timeline, coverage counts, edge/read/write/residue/unresolved counts, bounded errors, and
  links to the generated run and proposal;
- accessible labels, keyboard behavior, status/alert regions, pending-state protection, and retained
  user input after recoverable failures.

**Acceptance criteria**

- client normalization, conditional fields, validation, request serialization, polling termination,
  error rendering, query invalidation, and navigation tests pass;
- the visible application at the configured product URL no longer presents seeded collection as the
  only collection capability;
- production runtime configuration cannot submit an arbitrary local filesystem path.

### G5 — Prove the complete Petclinic product flow

**Status:** `IN_PROGRESS` — API and browser flows both proven; owner sign-off remains

`tests/integration/test_repository_collection_product_flow.py` drives the pinned checkout through
`POST /api/collections` and `GET /api/collections/{commandId}` — not a simulated fixture path — and
passes 4/4 against revision `88e37c15cf6fc8490b01bc3e8e2c800cec1ac272`:

- terminal run and proposal both `IN_REVIEW`; 15 edges, 10 reads, 5 writes, 8 bounded residue
  entries, zero unresolved;
- coverage 131 expected = 33 completed + 98 skipped, zero unsupported, zero failed;
- `runtimeStatus` explicitly `NOT_PROVIDED`;
- duplicate submission returns identical durable identities with a byte-identical database and
  evidence digest;
- neither the responses nor the retained evidence contain the checkout path, source, credentials or
  stack traces.

Writing this test found three real defects, all fixed: `terminal` was computed against a
non-existent `SUCCEEDED` command status so a finished collection never stopped polling; a duplicate
submission overwrote the recorded `ACCEPTED` outcome with `DUPLICATE`; and this document's expected
residue count was wrong.

The same pinned revision was also driven through the running application in headless Chrome. The
rendered result agreed with the retained result on every value: `COMPLETED` command, `ACCEPTED`
outcome, run `IN_REVIEW`, the eight stages `QUEUED` through `IN_REVIEW`, 15 edges / 10 reads /
5 writes / 8 residue / 0 unresolved, coverage 131 expected with 33 completed, 98 skipped, 0
unsupported and 0 failed, runtime `NOT_PROVIDED`, analysis `COMPLETE`. Both links resolved: the run
timeline rendered 8 stages and the proposal page rendered `proposal-…` in `IN_REVIEW`.

Remaining before `COMPLETE`: owner sign-off.

Run the actual pinned Spring Petclinic repository through the collection API and React workflow,
not through a simulated fixture path.

**Required outcome**

- accepted or safely reused durable collection;
- terminal run and proposal in `IN_REVIEW`;
- 15 edges: 10 reads and 5 writes;
- 8 bounded residue entries, all `ignored-schema-statement`, and zero unresolved invocations
  (this document previously said "zero residue", which contradicted the established oracle in
  `tests/integration/test_spring_petclinic_repository.py`; the verified value is 8);
- coverage: 131 expected, 33 completed, 98 skipped, zero unsupported, zero failed;
- runtime status explicitly `NOT_PROVIDED`, never silently treated as runtime corroboration;
- duplicate submission returns the same durable identities and identical database/evidence state.

**Acceptance criteria**

- automated API-level product-flow test passes against the exact checkout;
- browser smoke verifies submission, progress, counts, run timeline, and proposal navigation;
- retained evidence contains no external source, checkout path, credentials, raw queries, secrets, or
  nondeterministic timestamps;
- user-facing and retained results agree on revision, counts, coverage, and terminal state.

### G6 — Produce live AWS deployment and operational evidence

**Status:** `EXTERNAL_REQUIRED` after G3–G5

Structural synthesis is complete but is not live cloud evidence. Run the approved deployment and
smoke workflow in an authorized AWS account/profile without enabling production runtime collection.

**Required evidence**

- successful production-shaped CDK synth and immutable package metadata;
- ephemeral AWS deploy with expected private networking, IAM/KMS, queues, workflows, stores,
  compute, API, SPA, alarms, and cleanup ownership;
- signed event to durable command, workflow artifacts, reviewable proposal, approved/fenced graph
  publication, and queryable lineage/impact result;
- retry/redrive, DLQ, stale lease, duplicate, partial publication, rollback, and recovery smoke paths;
- runtime production hard-deny and kill switch proven while non-production/runtime ATDD remains
  independently controllable;
- cleanup completes without leaving unowned resources.

**External prerequisites**

- approved account, profile, region, network, domain, identity, GitHub/Jenkins/TAS integration,
  catalog, paging, Bedrock, retention, and security-policy context values;
- explicit opt-in for deployment commands and any billable or externally visible test traffic.

`AWS_REQUIRED` and `NOT_CONFIGURED` remain the only truthful outcomes until these prerequisites and
results exist.

### G7 — Close the pull-request review loop and merge to main

**Status:** `IN_PROGRESS`

Draft PR #2 exists and is merge-clean, but it is not a completion signal. The branch must first
contain G1–G6 evidence and be reconciled with current `main`.

**Acceptance criteria**

- branch incorporates the current target branch without losing user-owned work;
- full local verification, acceptance, workflow parity, packaging, build, and CDK synthesis pass;
- required live AWS gates are `PASS`, or unresolved external gates remain explicitly documented and
  the user approves that boundary before merge;
- every actionable GitHub and Codex bot review thread is reproduced or validated, addressed with
  evidence, replied to, and resolved;
- checks are green and no new actionable threads appear after the final push;
- the draft is marked ready only after the above gates pass;
- the reviewed head is merged into `main`, and the remote `main` commit is verified to contain the
  final head and canonical goal/architecture artifacts.

CodeRabbit must not be invoked or used for local review. The automatic GitHub/Codex review loop is
handled through PR-native checks and thread-aware review state.

## 5. Dependency and execution order

```text
G1 target architecture ───────────────────────────────┐
                                                      │
G2 remote Git acquisition → G3 collection API → G4 UI/status → G5 product acceptance
                                                      │
G6 live AWS deploy and smoke proof ←──────────────────┘
                                                      │
G7 review closure, ready state and merge ←────────────┘
```

G1 can proceed while G2 is being completed. G3 must not bypass G2 source safety. G4 must consume the
G3 contract rather than invoke CLI or filesystem behavior. G5 requires the exact G2–G4 path. G6
uses the same application contracts and must not introduce an AWS-only semantic fork. G7 is the
final convergence gate.

## 6. Required verification matrix

| Goal | Minimum fresh verification before completion |
|---|---|
| G1 | Documentation assertions, Mermaid syntax/render, offline HTML visual inspection, target-only architecture review. |
| G2 | Remote/adversarial acquisition suite, acquisition policy suite, local Git security suite, Task 1 collection/CLI regressions, opted-in exact GitHub checkout. |
| G3 | FastAPI contract tests, application-neutral product API tests, AWS entry-point tests, duplicate/no-effects API proof. |
| G4 | Typed client tests, form/status component tests, workspace tests, TypeScript build, browser accessibility and behavior smoke. |
| G5 | Exact Petclinic product-flow test, retained-evidence proof, duplicate effect snapshot, browser result verification. |
| G6 | `make verify`, acceptance smoke, workflow check, package/build, all-stack synth, ephemeral deploy/smoke/cleanup and retained AWS evidence. |
| G7 | Clean scoped worktree, branch/base reconciliation, green PR checks, zero unresolved actionable review threads, verified merge commit on `main`. |

Relevant commands include:

```bash
make verify
make acceptance-smoke
make workflow-check
make synth
./scripts/run_real_repository_acceptance.sh
./scripts/deploy_ephemeral_aws.sh
./scripts/smoke_ephemeral_aws.sh
./scripts/cleanup_ephemeral_aws.sh
```

AWS commands run only with the approved environment, credentials, context, and explicit opt-in.

## 7. Safety boundaries and non-goals

- Do not invent enterprise context values or convert `AWS_REQUIRED`/`NOT_CONFIGURED` into passes.
- Do not enable runtime collection in production as part of ATDD or deployment smoke.
- Do not send source code, raw queries, secrets, credentials, local paths, or unbounded process output
  to retained evidence, API errors, telemetry, LLM services, or pull-request comments.
- Do not execute repository build scripts, Maven/Gradle plugins, hooks, credential helpers, or
  arbitrary repository code during static collection.
- Do not claim generic support beyond the versioned analyzer cells and compatibility matrices that
  have real acceptance evidence.
- Do not split the application into premature network services solely to mirror AWS components;
  preserve application ports and deployable-unit boundaries.
- Do not stage or commit the user-owned `.gitignore` change unless the user explicitly requests it.
- Do not use CodeRabbit.

## 8. Normative supporting artifacts

- [Architecture refactor design](plans/2026-08-05-lineage-collection-architecture-refactor-design.md)
- [Architecture refactor implementation plan](plans/2026-08-05-lineage-collection-architecture-refactor.md)
- [Production AWS application design](plans/2026-08-08-production-aws-application-completion-design.md)
- [Production AWS application plan](plans/2026-08-08-production-aws-application-completion.md)
- [Runtime instrumentation design](plans/2026-08-07-runtime-lineage-instrumentation-design.md)
- [Runtime implementation plan](plans/2026-08-07-runtime-lineage-instrumentation.md)
- [Java/Spring repository design](plans/2026-08-09-real-java-spring-repository-lineage-design.md)
- [Java/Spring implementation plan](plans/2026-08-09-real-java-spring-repository-lineage.md)
- [Repository collection UI/API design](plans/2026-08-10-repository-collection-ui-api-design.md)
- [Repository collection UI/API plan](plans/2026-08-10-repository-collection-ui-api.md)
- [Executable acceptance policy](acceptance/lineage-platform-acceptance.md)
- [Implementation and evidence coverage](prototype-coverage.md)
- [Build-ready PRDs](build-prds/README.md)
- [Component PRDs](component-prds/00-system-context.md)
- [Target AWS architecture](architecture/lineage-platform-target.md)

## 9. Final definition of done

The remaining goal is complete only when all of the following are true:

- G1–G7 are `COMPLETE`, with `EXTERNAL_REQUIRED` closed or explicitly accepted by the user as a
  documented post-merge external gate;
- the target AWS Mermaid and offline HTML architecture are canonical and current;
- exact remote Git collection is safe, bounded, deterministic, and wired to the durable application;
- the collection API and React product run the exact Petclinic flow without a seeded placeholder;
- local, real-repository, browser, workflow, package, build, synthesis, and required live AWS
  verification evidence is fresh and retained;
- no required check is failing and no actionable GitHub/Codex review thread remains unresolved;
- the final reviewed commit is merged into `main` and the remote `main` branch is verified;
- the progress journal records the final commit, PR, merge, evidence, external outcomes, and any
  explicitly accepted residual risk.

Until those conditions hold, the PR remains a draft and the goal remains active.
