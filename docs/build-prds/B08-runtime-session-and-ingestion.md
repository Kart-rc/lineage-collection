# B08 — Runtime session and ingestion plane

Normative requirements: [L06 runtime plane](../component-prds/06-runtime-observation-plane.md) and
[L12 security/operations](../component-prds/12-security-observability-operations.md).

## Ownership

Track C owns grant/session lifecycle, token scope, validation, normalization, ordered ingestion,
closing manifests and late reconciliation. Security owns production hard-deny policy.

## Boundary

Grant short-lived, dataset/artifact-scoped non-production sessions; validate metadata-only
OpenLineage/SDK/OTel observations; persist accepted evidence; close with an honest manifest.

## Contracts

Strict session and observation schemas bind repository, environment, artifact, datasets, sequence,
mechanism and granularity. Generic OTel connectivity is not authoritative element lineage.

## State and failure model

`GRANT -> READY -> OBSERVING -> DRAIN -> CLOSED`; terminal outcomes are complete, incomplete,
expired or revoked. Duplicate identity is idempotent; conflicting duplicate, sequence, scope,
prohibited field or production environment is rejected and audited.

## Data ownership

B08 owns session/control records, normalized observations, counters, manifests and reconciliation
cursor. Instrumentation configuration belongs to B09; consolidated edge truth belongs to B10.

## Infrastructure bill of materials

Grant/validation Lambda functions, API Gateway/private endpoint, Kinesis encrypted stream,
DynamoDB session/idempotency tables with TTL/PITR, S3 runtime evidence, DLQ/reconciliation schedule,
KMS, WAF/IAM denies, logs and alarms.

## Local adapter

SQLite sessions/observations, signed local tokens, deterministic clocks and contract fixtures prove
lifecycle, scope, idempotency and metadata-only enforcement without production collection.

## Security and privacy

Hard deny every production-like environment at IAM and validator layers. Use least-privilege
workload identity, short TTL, prohibited-field scanning, no values/secrets/source and audited revoke.

## SLOs

Zero accepted production or prohibited-data observation. Drain/overhead/throughput targets need the
approved integration environment; incomplete close is visible rather than an SLO success.

## Observability

Emit grants/expiry/revoke, attempted/accepted/rejected/duplicate, stream lag/throttle, session
completion, artifact join, drain duration and production-deny probe results.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B08-AC-001 | L06 scoped metadata-only runtime intake | Expired/revoked/malformed/prohibited/artifact-mismatched/production observations persist no evidence; duplicate valid observations converge | Every PR security gate; weekly live negative |

## Deployment and rollback

Canary validators before granting new sessions; schema/token changes are versioned. Emergency rollback
revokes grants and drains to incomplete manifests; it never silently discards accepted evidence.

## Dependencies

B01, B03, B05 and B09 emitters; environment taxonomy, identity provider, retention and stream quotas
are CTX seams.

## Definition of Ready

Environment deny policy, token issuer, prohibited-field list, session TTL/buffer limits, schemas and
reconciliation owner are approved.

## Definition of Done

Lifecycle/security/idempotency fixtures pass; denial is enforced twice; incomplete drain is visible;
alarms/runbooks and `B08-AC-001` evidence exist.
