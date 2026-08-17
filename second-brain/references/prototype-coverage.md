---
type: Reference
title: Implementation and Evidence Coverage
description: The honest per-component implementation matrix mapping L01-L16 to their current evidence levels, deliberate gaps, and the pinned real-repository proofs.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/prototype-coverage.md
tags: [lineage, coverage, evidence, status, petclinic]
timestamp: 2026-08-14T11:30:00Z
---

# Implementation and Evidence Coverage

An implementation matrix, explicitly not a production-readiness declaration. Evidence statuses:
`LOCAL_PASS`, `SYNTH_PASS`, `AWS_REQUIRED`, `NOT_CONFIGURED`, `LOCAL_REAL_REPOSITORY_PASS` — and
`AWS_REQUIRED`/`NOT_CONFIGURED` never mean `PASS`.

## Where each domain stands

- Fully implemented with `LOCAL_PASS`: L01 URN/resolver, L03 orchestration, L07 consolidation, L09 proposals/review, L13 classification; L02 intake, L08 stores, L10 fenced publication, L11 APIs/UI/PR-gate, L12 security signals, L15 resiliency harness add `SYNTH_PASS` with live behavior `AWS_REQUIRED`.
- L04 SCA: deterministic Python AST pack plus a closed Tree-sitter Java/Spring Data JPA/PostgreSQL cell with element-level column and entity-field lineage; proven by the Spring Petclinic `LOCAL_REAL_REPOSITORY_PASS`. Known fail-closed limits: wildcard framework imports without visible jars, unnamed `CREATE INDEX`, `@Table` from a constant — each yields `INTEGRATION_REQUIRED` by design.
- L05 LLM gateway is interface-only (skip/no-fabrication contracts pass locally; everything external `NOT_CONFIGURED`). L06 runtime observation has full local contracts with enterprise listeners `NOT_CONFIGURED`. L14 infrastructure is `SYNTH_PASS` with ten CDK stacks and deterministic OCI packages.

## Real-checkout results

- Pinned official Petclinic (`88e37c15…`): 15 static edges (10 reads / 5 writes), 131 paths as 33 completed + 98 skipped, deterministic evidence, duplicate no-effect.
- `spring-petclinic-microservices` (`305a1f13…`, 208 files, 8 modules) scores 9/9 against the prototype expectation: 14 edges, cross-service REST interactions both directions, blast radius composed over 9 datasets. Six boundaries were closed narrowly (bounded wildcard-import resolution, multi-module Maven parents, MySQL session DDL as inventory, per-module profile schemas, cross-entity `@Query`, other-profile schema skipping) — each a stated trade-off, not a guess.
- Schema sources are replayed, not accumulated: profile schema wins over Flyway migrations over Liquibase changelogs; unmodelled statements (renames especially) mark the schema incomplete and fail resolution closed.

## Gate summary

Backend/React/CDK/package gates are `LOCAL_PASS`/`SYNTH_PASS`; local acceptance smoke is 3 `PASS` +
5 `AWS_REQUIRED`; ephemeral AWS deploy and all production/scale/DR claims remain `AWS_REQUIRED`;
enterprise integrations remain `NOT_CONFIGURED`. The prototype-expectation vocabulary is extracted
from the source document by script and asserted by tests, so prototype drift breaks the build.

## Related

* [Lineage Collector](/projects/lineage-collector.md) - the project whose truthful status this matrix records
* [Lineage Platform Executable Acceptance Specification](/references/lineage-platform-acceptance.md) - defines the status vocabulary and manifests this matrix reports in
* [Lineage Platform Target AWS Architecture](/references/lineage-platform-target.md) - renders these evidence levels as its component status legend
* [SCA Engine](/references/sca-engine.md) - the normative requirements behind the L04 analyzer-cell coverage described here
* [Throughline Prototype: Agentic Product Document](/references/prototype-throughline-agentic.md) - the prototype document whose machine-extracted vocabulary this matrix's 18/18 and 9/9 alignment scores are measured against
* [Lineage Platform Remaining Goals](/projects/remaining-goals.md) - the plan for converting the remaining `AWS_REQUIRED` rows into evidence

## Citations

1. [prototype-coverage.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/prototype-coverage.md)
