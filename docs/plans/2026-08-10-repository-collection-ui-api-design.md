# Repository Collection UI and API Design

**Date:** 2026-08-10
**Status:** Approved

## Goal

Expose the existing exact-revision lineage collection pipeline through the product API and React application. A user can submit either a bounded local Git checkout for development or a Git URL plus immutable commit, follow the durable orchestration stages, inspect coverage and lineage counts, and continue to the generated run and review proposal.

## Decision

Use an asynchronous durable command with polling.

The API records and validates the collection request, drives the existing command/outbox/lease workflow, and returns an HTTP `202` representation containing stable identifiers and a status URL. The UI polls that resource until it reaches a terminal state. This contract works with the local SQLite/file adapters and can map to API Gateway, DynamoDB, S3, SQS, Lambda, and Step Functions without changing the browser contract.

A synchronous endpoint was rejected because source acquisition and analysis can exceed browser, API Gateway, or Lambda request limits. WebSockets were deferred because polling provides the required correctness and visibility without introducing a connection-management boundary.

## API Contract

### Submit collection

`POST /api/collections`

The request contains:

- a discriminated source descriptor:
  - `LOCAL_CHECKOUT`: canonical checkout path, configured origin, and exact revision;
  - `GIT`: repository URL and exact revision;
- repository, environment, and system identity;
- analyzer pack, ruleset, and schema profile.

The response contains:

- collection and durable command identifiers;
- run and proposal identifiers when available;
- outcome and current status;
- a stable status URL;
- orchestration stages;
- coverage and lineage-count summaries;
- bounded machine-readable failure details.

Equivalent requests are idempotent and return the same durable work identity without creating duplicate evidence or proposals.

### Read collection status

`GET /api/collections/{commandId}`

The response presents the durable command state and, as they become available, the run timeline, coverage manifest, analysis counts, proposal state, and runtime evidence status. Unknown identifiers return a bounded `404` response. Invalid source descriptors return structured validation errors without exposing local paths, credentials, raw source, or command output.

## Source Acquisition

All source modes produce the same immutable `RepositorySnapshot` application model before classification or analysis.

### Local checkout adapter

The local adapter reuses the hardened exact-checkout reader. It verifies the canonical non-symlink root, configured origin, exact `HEAD`, clean tracked scope, bounded file count and bytes, stable object identities, and supported repository shape.

Local filesystem paths are disabled by default outside the explicit development configuration. Production requests cannot use this source mode.

### Git URL adapter

The local runtime acquires the exact revision into a private temporary directory using a trusted Git executable, bounded time and output, a minimal environment, and no inherited credential-helper or hook configuration. The resulting checkout is passed to the same hardened snapshot reader and is removed after the immutable snapshot has been materialized.

The production adapter will authenticate through the approved Git integration, materialize an immutable source artifact in S3, verify its digest, and submit that artifact reference to workers. The public API contract remains unchanged.

## Application Flow

1. The UI validates required fields and submits a collection request.
2. The API validates policy, source identity, analyzer compatibility, and bounded input sizes.
3. Source acquisition produces an immutable exact-revision snapshot.
4. Intake deterministically creates or reuses the durable command and transactional outbox record.
5. Workers claim the command through a lease and execute classification, analysis, resolution, evidence storage, consolidation, and proposal stages idempotently.
6. The UI polls the collection status and renders the stage timeline, coverage, counts, and any bounded failure.
7. When complete, the UI links to the run detail and review proposal.

The local prototype may drive the durable command to completion within the local API process, but the endpoint still exposes the asynchronous `202` resource contract. In AWS, API submission and worker execution are separate runtimes.

## React Experience

The Operations page replaces the placeholder as the primary action with a **Repository collection** panel containing:

- source-mode selection;
- local checkout path or Git URL;
- exact revision;
- repository, environment, and system;
- analyzer pack, ruleset, and schema profile;
- submit and clear controls.

After submission it shows:

- accepted, duplicate, reused, running, completed, or failed state;
- the existing orchestration stage timeline;
- expected, completed, skipped, unsupported, and failed coverage;
- total edge, read, write, residue, and unresolved counts;
- links to run and proposal details.

The seeded demo remains available as a clearly labelled secondary development action.

## Failure and Security Behavior

- Local paths are rejected unless the development adapter is explicitly enabled.
- URL schemes, revisions, field lengths, repository size, file size, total bytes, execution time, and subprocess output are bounded.
- Source acquisition does not inherit ambient Python paths, Git configuration, credential helpers, hooks, or user-controlled executables.
- API errors contain stable reason codes and correlation IDs, not source content, local paths, credentials, raw Git output, or stack traces.
- Failed commands remain queryable and do not publish partial graph state.
- Duplicate requests reuse prior work and do not duplicate edges, evidence, or proposals.
- Temporary checkouts are private and removed on success and failure.

## Verification

Backend tests cover request validation, source-mode policy, exact revisions, analyzer selection, idempotent duplicates, status projection, bounded failures, safe source acquisition, and cleanup.

Frontend tests cover both source modes, conditional fields, request serialization, validation, polling, progress rendering, failure rendering, and navigation to run and proposal details.

The end-to-end acceptance test uses the pinned Spring Petclinic checkout at revision `88e37c15cf6fc8490b01bc3e8e2c800cec1ac272` and requires:

- terminal completion with a review proposal;
- 15 lineage edges;
- 10 reads and 5 writes;
- zero residue and zero unresolved items;
- 131 expected files accounted for as 33 completed and 98 skipped;
- duplicate submission returning the same durable identifiers and no additional effects.

The final milestone also runs the full backend, frontend, infrastructure, build, workflow, and AWS synthesis gates.
