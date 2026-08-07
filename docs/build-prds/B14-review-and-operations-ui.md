# B14 — Review and operations UI

Normative requirements: [L11 APIs, review UI and PR gate](../component-prds/11-apis-review-ui-and-pr-gate.md)
and [L12 operations and telemetry](../component-prds/12-operations-telemetry-and-recovery.md).

## Ownership

Track D owns the reviewer and operator experience, accessible state rendering and typed API clients.
Domain owners retain authority over confidence, policy and operational decisions shown by the UI.

## Boundary

Render server-authoritative lineage, proposal, gate and operational state. The browser never computes
confidence, invents completeness or applies optimistic approval, publication or recovery decisions.

## Contracts

Typed API models cover lineage, impact, proposals, checks, operations and resilience status. Every
view carries namespace/version, correlation and freshness; mutations carry expected version/idempotency.

## State and failure model

Every surface explicitly renders loading, empty, stale, incomplete, unauthorized, concurrent-change,
degraded and unavailable states. A failed mutation refreshes authoritative state before retry.

## Data ownership

B14 owns static assets and a transient browser cache only. Proposals, decisions, evidence, pointers,
coverage, run state and operational controls remain server-owned and are never browser truth.

## Infrastructure bill of materials

Versioned web assets in S3/CloudFront or the approved hosting service, TLS, WAF, identity integration,
CSP, access logs, real-user monitoring, deploy alarms and immutable release metadata.

## Local adapter

Vite/React uses the FastAPI adapter and deterministic mocks for all states. The production build runs
without AWS credentials and can be served locally for keyboard, accessibility and workflow checks.

## Security and privacy

Server authorization protects domains and actions; CSP, CSRF controls and secure cookies protect the
client. Do not persist credentials, protected graph bodies or evidence contents in logs/local storage.

## SLOs

Critical flows meet accessibility and state-fidelity gates. Production performance/availability SLOs
require real-user and deployed synthetic evidence; local timings cannot satisfy them.

## Observability

Emit route/API latency, client errors, conflicts, rendered resilience state, accessibility failures,
release version and correlation ID without sensitive graph or evidence payloads.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B14-AC-001 | L11 accessible, truthful review/operations UX | Keyboard and automated checks exercise loading, empty, stale, incomplete, unauthorized, conflict, degraded and unavailable states without client-authored decisions | Every PR UI/E2E gate |

## Deployment and rollback

Publish content-addressed assets, canary the release and switch an atomic version pointer. Rollback
restores the previous asset/API-compatible release without mutating authoritative workflow state.

## Dependencies

B01 contracts, B11 proposals, B13 APIs/gates and B15 resilience status. Identity and hosting remain
approved CTX seams until production configuration is supplied.

## Definition of Ready

API schemas, role/action matrix, complete state catalogue, accessibility target, browser support,
hosting/identity choice and redaction policy are approved.

## Definition of Done

Typed-client, state, accessibility, mutation-conflict and build tests pass; CSP/deploy/rollback are
documented; `B14-AC-001` evidence is retained.
