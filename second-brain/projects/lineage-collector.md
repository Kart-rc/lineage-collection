---
type: Project
title: Lineage Collector
description: A locally runnable, production-shaped platform for evidence-first data-lineage collection with signed intake, deterministic analysis, human review, and fenced publication.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/README.md
tags: [lineage, platform, project, evidence-first]
timestamp: 2026-08-14T11:30:00Z
---

# Lineage Collector

## What it is

An evidence-first lineage collection platform. It accepts signed repository events, runs
deterministic Baseline and Incremental collection, validates optional runtime evidence, creates and
reviews proposals, publishes with fencing, handles exact-artifact deployment promotion, evaluates a
bounded read-only PR gate, and serves version-pinned lineage and impact queries.

The default operator path is local: FastAPI + SQLite + a write-once object directory on the backend,
React + TypeScript + Vite on the frontend. The same repository also ships nine Lambda handlers, an
SCA Fargate worker, four generated Step Functions workflows, AWS adapters, CDK stacks, deterministic
OCI packaging, and a guarded ephemeral-AWS verification flow. Local use needs no AWS credentials or
LLM key.

## Key operating facts

- `make setup` / `make dev` runs the stack; UI at :5173, API at :8000; `make reset` restores the deterministic demo.
- `collect-checkout` analyzes canonical committed Git blobs for a Java/Spring checkout without executing Maven, Gradle, tests, hooks, or repository code; collection stops at `IN_REVIEW` and never auto-publishes.
- The pinned Spring Petclinic revision `88e37c15…` is the acceptance oracle: 15 static edges (10 reads, 5 writes), zero unresolved invocations, 131 tracked paths (33 completed, 98 skipped), runtime `NOT_PROVIDED`; Petclinic constants exist only in the oracle, never in production analyzer code.
- Every tracked path must land in exactly one disposition (completed/skipped/unsupported/failed); `COMPLETE` is impossible with unsupported or failed paths.
- Workflow definitions in Python are the authority; `infra/workflows/` ASL files are deterministic exports checked by `make workflow-check`.
- No AWS command runs implicitly; ephemeral deploy/smoke/cleanup each require explicit opt-in variables, and honest non-runs are reported as `AWS_REQUIRED`, never as passes.

## Repository map

`apps/api` (domain/application core, FastAPI, local and AWS adapters), `apps/web` (React control-room
UI), `infra` (CDK, packaging, generated ASL), `packages/contracts` (strict JSON Schemas),
`fixtures`, `scripts`, `tests`, `docs`.

## Related

* [Lineage Platform Remaining Goals](/projects/remaining-goals.md) - the G1-G7 completion state and owner-blocked steps for this project
* [Lineage Platform Target AWS Architecture](/references/lineage-platform-target.md) - the canonical production AWS target this local implementation is shaped toward
* [Lineage Platform Executable Acceptance Specification](/references/lineage-platform-acceptance.md) - the evidence labels and release gates the README's commands feed
* [Implementation and Evidence Coverage](/references/prototype-coverage.md) - the honest per-component evidence matrix behind the README's claims
* [Build-ready Lineage Platform PRDs Overview](/references/build-prd-overview.md) - the B01-B16 build order that structures delivery

## Citations

1. [README.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/README.md)
