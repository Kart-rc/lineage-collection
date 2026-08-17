---
type: Reference
title: B14 Review and Operations UI
description: Delivers the accessible reviewer and operator web experience that renders server-authoritative lineage, proposal, gate, and operational state without ever computing decisions in the browser.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B14-review-and-operations-ui.md
tags: [lineage, build-prd, ui, review, accessibility]
timestamp: 2026-08-14T11:30:00Z
---

# B14 Review and Operations UI

Track D's human-facing surface (normative sources: L11 APIs/review UI/PR gate, L12 operations and
telemetry). Its defining stance is that the browser is a *renderer*, never an authority: it never
computes confidence, invents completeness, or applies optimistic approval, publication, or recovery
decisions. Domain owners retain authority over everything the UI shows.

## What it delivers

The reviewer and operator experience, accessible state rendering, and typed API clients covering
lineage, impact, proposals, checks, operations, and resilience status. Every view carries
namespace/version, correlation, and freshness; every mutation carries expected version and an
idempotency key.

## Key invariants

- **Truthful state rendering**: every surface explicitly renders loading, empty, stale,
  incomplete, unauthorized, concurrent-change, degraded, and unavailable states — exercised by
  keyboard and automated accessibility checks (B14-AC-001, per-PR UI/E2E gate).
- A failed mutation refreshes authoritative server state before retry — no optimistic client
  truth.
- B14 owns only static assets and a transient browser cache; proposals, decisions, evidence,
  pointers, coverage, run state, and operational controls remain server-owned.
- No credentials, protected graph bodies, or evidence contents in logs or local storage; CSP,
  CSRF controls, and secure cookies on the client, with authorization enforced server-side.

## Delivery model

Versioned, content-addressed web assets on S3/CloudFront (or the approved host) with canary
releases and an atomic version-pointer switch; rollback restores the previous API-compatible
release without touching workflow state. The local adapter is Vite/React against the FastAPI
adapter with deterministic mocks for all states — the production build runs without AWS
credentials for keyboard/accessibility/workflow checks.

## Constraints and seams

Production performance/availability SLOs require real-user and deployed synthetic evidence; local
timings cannot satisfy them. Identity and hosting remain approved CTX seams until production
configuration is supplied. Definition-of-Ready includes API schemas, role/action matrix, a
complete state catalogue, accessibility target, browser support, and redaction policy.

## Related

* [APIs, Review UI and PR Gate](/references/apis-review-ui-and-pr-gate.md) - the normative L11 component PRD defining the review UI requirements this build unit implements.
* [B13 Query, Impact and PR Gate](/references/b13-query-impact-and-pr-gate.md) - provides the query and gate APIs the UI's typed clients consume.
* [B11 Proposal, Review and Policy](/references/b11-proposal-review-and-policy.md) - owns the server-authoritative proposal/decision state the UI renders for reviewers.
* [B15 Operations, Telemetry and Recovery](/references/b15-operations-telemetry-and-recovery.md) - supplies the resilience status the operations views display.
* [Security, Observability and Operations](/references/security-observability-operations.md) - normative source for the operational and telemetry surfaces (L12) the UI exposes.

## Citations

1. [B14-review-and-operations-ui.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B14-review-and-operations-ui.md)
