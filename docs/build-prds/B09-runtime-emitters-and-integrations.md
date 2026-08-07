# B09 — Runtime emitters and integration kits

Normative requirements: [L06 runtime integrations](../component-prds/06-runtime-observation-plane.md).

## Ownership

Track C owns OpenLineage facets, OTel mapping, custom SDKs, Spark/Dask kits, compatibility matrices,
buffer/drain behavior and integration documentation. Platform teams own install paths.

## Boundary

Capture metadata-only runtime relationships at supported framework boundaries, bind them to the
deployed artifact/session and submit bounded observations to B08. Emitters never assign confidence.

## Contracts

OpenLineage column facets, custom SDK field mappings and OTel semantic-convention mappings retain
distinct mechanism/granularity. Kits include session ready/observe/drain/close and checksum support.

## State and failure model

Clients buffer within explicit limits, preserve per-dataset ordering, retry idempotently and close
incomplete on loss/throttle. Unsupported topology or generic connectivity is labeled, not upgraded.

## Data ownership

B09 owns emitter packages, mappings, integration manifests and client-side counters. B08 owns
accepted observations/manifests; application data values are never owned or emitted.

## Infrastructure bill of materials

Signed Python/JVM/Node packages, OpenLineage listener artifacts, OTel collector configuration,
private intake endpoints, artifact registry, SBOM/signatures, compatibility CI and overhead canary.

## Local adapter

Representative OpenLineage, OTel and SDK JSON fixtures plus fake transport/buffer/drain prove
mapping and loss semantics. They do not prove platform installation or runtime overhead.

## Security and privacy

SDK-side allowlists and prohibited-field checks precede transport. Use workload identity, TLS,
bounded logs and no SQL parameters, record values, secrets or production session support.

## SLOs

Default overhead is proposed at at most 5%, but remains `AWS_REQUIRED`/integration-required until
measured on approved workloads. Closing-manifest completeness must state all loss.

## Observability

Expose emitted/buffered/dropped/retried counts, mapping version, mechanism/granularity, artifact bind,
session close, overhead and collector/intake lag.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B09-AC-001 | L06 mechanism fidelity and close | OpenLineage, SDK and OTel fixtures retain distinct semantics and exact artifact binding; loss produces an incomplete closing manifest; overhead evidence names environment | Every PR contracts; integration overhead gate |

## Deployment and rollback

Publish signed packages, test compatibility, opt-in canary by platform and preserve prior versions.
Rollback disables instrumentation or restores the prior kit while draining sessions honestly.

## Dependencies

B01 contracts and B08 session/intake. Platform install paths, OTel topology ownership, workload
identity and supported framework versions are CTX seams.

## Definition of Ready

Framework/version matrix, mapping authority, metadata allowlist, buffer policy, installation owner,
overhead workload and rollback switch are defined.

## Definition of Done

Packages/fixtures are signed and compatible; semantic distinctions survive ingestion; loss/overhead
are evidenced in the correct environment; `B09-AC-001` is retained.
