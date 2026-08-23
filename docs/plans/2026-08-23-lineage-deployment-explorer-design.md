# Lineage Deployment Explorer Design

**Date:** 2026-08-23
**Status:** Approved

## Goal

Create an offline, interactive HTML explainer that makes the repository's lineage collection and
deployment architecture understandable to a mixed audience. The explainer must connect the working
local product to the production-shaped AWS target without confusing local executable evidence,
offline CDK synthesis, and live cloud proof.

## Audience and presentation strategy

The primary audience is mixed: product and architecture readers need a guided narrative, while
engineers and operators need exact deployable units, artifacts, stage ownership, source links, and
known gaps. The page therefore starts with short selectable journeys and progressively reveals the
implementation details in component and artifact drawers.

The visual direction is an industrial architecture flight deck: dark ink and blueprint surfaces,
IBM Plex Sans/Mono from the repository's offline font asset, cyan signal paths, amber warnings, and
green verified-state markers. Motion is used only to trace a selected flow and respects
`prefers-reduced-motion`.

## Chosen approach

Create a standalone, dependency-free page at
`docs/architecture/lineage-deployment-explorer.html`. It will contain semantic HTML, CSS, and
plain JavaScript in one file and import only the repository's existing offline font stylesheet.
This keeps the artifact directly openable from disk, avoids requiring the React application or a
documentation server, and leaves the generated canonical architecture rendering untouched.

Two alternatives were rejected:

- Extending `lineage-platform-target.html` would couple hand-authored interaction to a generated
  artifact whose Mermaid source is intentionally authoritative.
- Adding a React route would mix documentation with the product runtime and require build tooling
  merely to inspect the architecture.

## Information architecture

### 1. Orientation

The hero states three truths immediately:

- local FastAPI, SQLite, filesystem evidence, and React behavior are executable and tested;
- the AWS topology is packaged and synth-verified, but live deployment evidence remains
  `AWS_REQUIRED`;
- production AWS collection submission is currently `NOT_CONFIGURED`, even though the status and
  query surfaces exist.

A compact legend defines `verified`, `synthesized`, `partial`, `planned`, `NOT_CONFIGURED`, and
`AWS_REQUIRED`.

### 2. Guided journeys

Four selectable journeys animate one step at a time and update a synchronized narrative panel:

1. **SCA collection** — exact revision acquisition, classification and coverage, Fargate analysis,
   immutable evidence, deterministic consolidation, proposal, review, fenced publication.
2. **Runtime corroboration** — non-production session/lease, OTel/OpenLineage/custom SDK metadata,
   Kinesis, runtime-validation Lambda, evidence, and non-blocking consolidation.
3. **API and UI** — CloudFront/S3 React delivery, API Gateway, product Lambda, collection submit and
   status polling, runs/review/lineage/impact reads, and the production-submit gap.
4. **Deployment** — tests and build, Python wheel, web bundle, deterministic OCI images, digest
   metadata, ECR, ordered CDK stacks, Lambda canaries/Fargate task, smoke evidence, and guarded
   cleanup.

Each step exposes the payload or artifact crossing the boundary. Durable asynchronous handoffs and
synchronous calls use different line styles and labels.

### 3. Component inventory

Filterable cards describe the deployable and managed components:

- ten independently configured Lambda image targets: intake, control-stage, classification,
  coverage, runtime-validation, consolidation, proposal, publication, deployment, and product-api;
- the ARM64 ECS Fargate SCA callback task;
- four exported Standard Step Functions workflows plus the D1-D6 deployment Lambda workflow;
- EventBridge bus/archive, interactive/events FIFO SQS lanes, batch SQS lane, DLQs, Kinesis runtime
  stream, and the DynamoDB approval stream;
- S3 evidence/package/site buckets, four DynamoDB tables, two immutable ECR repositories, Neptune,
  KMS, CloudFront, API Gateway, backup, and recovery resources;
- CloudWatch alarms routed to an approved external SNS paging topic.

The page explicitly states that SNS is the alarm-notification path, not the lineage work transport;
SQS carries queued collection work and Kinesis carries runtime observations.

### 4. Deployment artifacts

An artifact rail shows the source and destination of each artifact:

- `apps/web/dist/` content-hashed JavaScript/CSS and shell files;
- the Python application wheel under `infra/dist/wheels/`;
- `lambda.oci.tar` for `linux/amd64` and `sca.oci.tar` for `linux/arm64`;
- Docker build metadata and archive/image digests;
- `runtime-build-metadata.json` and `package-manifest.json`;
- generated ASL and workflow contracts;
- the CDK CloudFormation assembly and stack outputs.

The deployment sequence mirrors `scripts/deploy_ephemeral_aws.sh`: validate explicit account and
network context and a clean commit; package images and web; deploy recovery/network/data first;
push exact digests to the immutable ECR repositories; deploy all stacks; retain outputs; run the
opt-in smoke; then require a separate destructive acknowledgement for cleanup.

### 5. Current-state matrix and source trail

Every component and journey is tagged with its evidence status. A source trail links to the exact
Dockerfile, CDK stack, Python workflow/handler, React component, deployment script, and canonical
architecture or coverage document supporting the description. A visible caveat explains the
current documentation count drift: the implementation defines ten Lambda targets.

## Interaction model

- Journey tabs switch the graph and explanatory copy without navigation.
- Previous, next, play, and reset controls trace the selected journey.
- Selecting a graph node or component card opens a detail drawer with responsibilities, inputs,
  outputs, deployment form, evidence state, and source links.
- Status and category filters reduce the component inventory and preserve result counts.
- A local/AWS comparison toggle changes adapter labels while leaving the domain stages stable.
- URL hashes preserve the selected journey or component for shareable offline navigation.
- All controls are keyboard accessible, focus is visible, the drawer is dismissible with Escape,
  and live announcements describe journey changes.

## Correctness and failure semantics

The content must preserve the platform invariants:

- repository code is never executed during static collection;
- runtime evidence is metadata-only, non-production, session-bound, and non-blocking;
- coverage completeness gates consolidation;
- human review is a gate before publication;
- publication uses monotonic fencing and immutable evidence references;
- retries reuse leases and idempotency determinants; exhausted work reaches DLQs;
- failed canaries do not advance aliases, and durable data is superseded rather than rolled back;
- missing live AWS proof remains `AWS_REQUIRED` rather than being inferred from synth.

The page itself fails gracefully: it is useful without JavaScript, never depends on a network
resource, and disables animation under reduced-motion preferences.

## Repository reconciliation

Update the introductory README count from nine to ten independently configured Lambda targets so it
matches `infra/lib/runtime-assets.ts`. Do not change the generated
`docs/architecture/lineage-platform-target.html` by hand.

## Verification

Verification will include:

- a focused documentation test asserting the new artifact exists and includes all required
  journeys, services, evidence statuses, source links, Lambda target names, and accessibility hooks;
- the focused documentation test and existing architecture parity check;
- HTML parsing and JavaScript syntax validation;
- a browser smoke at desktop and mobile widths covering journey selection, step playback, status
  filters, component drawer navigation, Escape dismissal, hash restoration, and console errors;
- visual inspection of desktop and mobile screenshots;
- the broader documentation suite and a final diff/status audit.
