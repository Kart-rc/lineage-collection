---
type: Reference
title: Lineage Platform Executable Acceptance Specification
description: The normative executable acceptance index defining Bxx-AC-nnn rows, test levels, release cadences, and the strict evidence-status vocabulary for the lineage platform.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/acceptance/lineage-platform-acceptance.md
tags: [lineage, acceptance, testing, evidence, gates]
timestamp: 2026-08-14T11:30:00Z
---

# Lineage Platform Executable Acceptance Specification

Version 1.0.0, normative. An assertion is not an accepted platform property until its required test
or drill produced a schema-valid `AcceptanceEvidenceManifest`; a local substitute can never prove an
AWS-only availability, scale, multi-AZ, security-control, or DR claim.

## Structure

- Each build unit B01-B16 owns an acceptance scope (e.g. B04 intake: authentication, durable ack, dedupe, ordering, fairness, replay, burst; B12 publisher: fence, stage/verify/swap, crash recovery, rebuild).
- Every acceptance row has a stable `Bxx-AC-nnn` ID with requirement links, Given/When/Then scenario, test level, versioned fixture and oracle, environment, thresholds, fault point, evidence, cadence, and release consequence. A row without an implemented test and fixture is `SPECIFIED`, not `PASS`.
- Nine test levels span unit/property through schema/contract, component, integration, E2E, load/fairness, fault/chaos, security/privacy, and disaster recovery.
- Release cadence: every PR (blocking), every deployment (stops/rolls back promotion), nightly (load/replay/rebuild), weekly (fence contention, production hard-deny), monthly (degradation rotation), quarterly (full warm-standby recovery with measured RPO/RTO).

## Evidence-status vocabulary

`PASS`, `FAIL`, `WAIVED`, `AWS_REQUIRED`, `NOT_CONFIGURED`, `HERMITIC_LOCAL_PASS`,
`LOCAL_REAL_REPOSITORY_PASS`, `LOCAL_REAL_REPOSITORY_REQUIRED`, `RUNTIME_NOT_PROVIDED`. The four
non-run statuses are never rendered or counted as `PASS`.

## The Petclinic proof (B06-AC-002 / B13-AC-002)

An opt-in proof over official Spring Petclinic at exact revision `88e37c15…`: the hardened runner
validates the checkout without executing repository code, drives the real durable `collect-checkout`
flow twice in fresh states plus a duplicate replay, and requires the exact oracle — 15 static edges
(10 reads, 5 writes), zero unresolved invocations, 131-path disposition (33 completed, 98 skipped),
`IN_REVIEW` proposal — with byte-identical manifest and evidence checksums across runs. The manifest
is write-once and contains no checkout paths, source, raw queries, timestamps, or credentials.
`make acceptance-smoke` stays hermetic and reports `LOCAL_REAL_REPOSITORY_REQUIRED` when the
checkout is absent.

A build unit is `READY` only with versioned schemas, a row/test/fixture/oracle for every P0 rule and
named failure, CDK assertions and least-privilege negatives per resource, fail-closed CTX seams,
current evidence, and no implicit or expired P0 waiver.

## Related

* [Lineage Collector](/projects/lineage-collector.md) - the platform whose delivery gates this specification defines
* [Build-ready Lineage Platform PRDs Overview](/references/build-prd-overview.md) - declares this acceptance policy normative for all B01-B16 build units
* [Implementation and Evidence Coverage](/references/prototype-coverage.md) - reports current outcomes in this specification's status vocabulary
* [Lineage Platform Target AWS Architecture](/references/lineage-platform-target.md) - its status legend maps directly onto these evidence labels

## Citations

1. [lineage-platform-acceptance.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/acceptance/lineage-platform-acceptance.md)
