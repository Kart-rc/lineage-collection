# B02 — Catalog snapshot and resolver packages

Normative requirements: [L01 resolver](../component-prds/01-urn-and-resolver-library.md) and
[L13 classification](../component-prds/13-repository-classification.md).

## Ownership

Track A owns snapshot ingestion, URN normalization, ambiguity quarantine, resolver packages and the
cross-language golden corpus. Catalog owners approve export and vocabulary CTX values.

## Boundary

Resolve repository/path/native identifiers against one explicitly pinned catalog snapshot and emit
canonical URNs or a typed ambiguity/not-found result. It never silently queries a moving catalog.

## Contracts

Inputs bind raw identifier, environment, system hint and snapshot ID. Outputs contain canonical URN,
candidate list, decision reason and evidence reference. Snapshot manifests are signed and checksummed.

## State and failure model

One active signed snapshot pointer references immutable snapshots. Refresh failure may use a visible
last-good snapshot only within approved age; expiry becomes `STALE`/unavailable, never fresh success.

## Data ownership

B02 owns catalog snapshots, resolver indexes, ambiguity records and golden mappings. Source catalog
records remain owned by the enterprise catalog.

## Infrastructure bill of materials

Versioned/Object-Locked S3 snapshot bucket, DynamoDB active-snapshot pointer and quarantine index,
refresh Lambda/EventBridge rule, KMS keys, alarms and package artifacts for Python/JVM/Node/Go.

## Local adapter

Fixture snapshots plus an in-process resolver implement the same pin, checksum and ambiguity
contract. The fixture is never presented as a production catalog integration.

## Security and privacy

Snapshot reads use scoped roles and private endpoints; exports exclude secrets and sampled data.
Snapshot signatures and KMS permissions prevent an analyzer from substituting identity truth.

## SLOs

Deterministic equality and last-good age are correctness gates. Resolver latency and refresh cadence
remain externalized until catalog and operations owners approve them.

## Observability

Publish snapshot age/checksum, resolution latency, exact/ambiguous/missing rates, last-good usage and
quarantine age by integration—not raw identifiers where access is restricted.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B02-AC-001 | L01 deterministic pinned resolution | Repeated and cross-language resolution over the golden corpus is byte-identical; refresh failure exposes signed last-good age; retain corpus/checksum report | Every PR and live canary |

## Deployment and rollback

Canary a new package and snapshot, compare golden results, then conditionally advance the pointer.
Rollback restores the prior package alias and snapshot pointer without deleting immutable snapshots.

## Dependencies

B01 schemas/correlation. Catalog export, vocabulary and ownership mappings are explicit CTX inputs.

## Definition of Ready

Export schema, snapshot signer, vocabulary, supported languages, age policy owner and golden corpus
are identified.

## Definition of Done

Packages agree on the corpus; ambiguity is quarantined; last-good behavior is visible; resources,
alarms, rollback and `B02-AC-001` evidence exist.
