# B01 — Contracts, schemas and correlation

Normative requirements: [L01 identity](../component-prds/01-urn-and-resolver-library.md),
[L08 evidence](../component-prds/08-evidence-store-and-cache.md), and
[L12 operations](../component-prds/12-security-observability-operations.md).

## Ownership

Track A owns the contracts package, compatibility policy, generators and correlation conventions.
Every producer owns conformance before release; consumers own explicit supported-version ranges.

## Boundary

A versioned library and schema bundle, not a network service. It defines envelopes and reference
types shared by local modules, Lambda handlers, Step Functions and external integration kits.

## Contracts

Strict JSON Schemas cover events, commands, stage references, evidence, coverage, runtime sessions,
proposals, approvals, packages, deployment outcomes and acceptance evidence. Correlation and
causation identifiers are required at every durable boundary. Compatibility is additive within a
major version; unknown major versions fail with a typed error.

## State and failure model

Schemas are immutable release artifacts. Generation is deterministic. Drift, an unknown major,
missing correlation or producer/consumer incompatibility blocks the affected build.

## Data ownership

B01 owns schema source, generated models, fixture versions and compatibility reports; it owns no
business records. Artifact digest and schema version identify every released bundle.

## Infrastructure bill of materials

CI schema lint/generation jobs, signed package artifacts, a package registry, provenance/SBOM,
compatibility reports and release attestations. Production registry/account values are `CTX` seams.

## Local adapter

Repository JSON files and Python/TypeScript model checks use the same fixtures and strictness as
published packages. Local imports never bypass schema validation at ingress boundaries.

## Security and privacy

Closed schemas reject undeclared fields where metadata-only guarantees apply. Fixtures contain no
credentials or production identifiers. Packages are signed, checksummed and dependency-scanned.

## SLOs

Contract validation must be deterministic and bounded; exact latency targets and package-retention
policy require owner approval. Compatibility drift is a merge-blocking correctness failure.

## Observability

Emit schema name/version, producer/consumer version, correlation coverage and typed rejection
counts without logging payload bodies or secrets.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B01-AC-001 | L01/L08/L12 contract compatibility | Old/current fixtures validate in every supported consumer; unknown incompatible versions and missing correlation are rejected; retain compatibility report and artifact digest | Every PR; merge block |

## Deployment and rollback

Publish packages immutably, canary consumers against the compatibility corpus, then advance aliases.
Rollback selects the prior signed package; schema records are never rewritten.

## Dependencies

No build-time domain dependency. B02-B16 depend on a frozen B01 release and generated-model check.

## Definition of Ready

Schema owner, semantic version, consumers, fixture corpus, compatibility window and unresolved CTX
owners are named.

## Definition of Done

Generators are reproducible; supported combinations pass; incompatible fixtures fail by design;
SBOM, signature and `B01-AC-001` evidence are retained.
