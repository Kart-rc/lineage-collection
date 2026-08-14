---
type: Note
title: OSS Java Lineage Measurement
description: "Measurement (2026-08-14): deterministic SCA+runtime collection across ten pinned OSS Java repos (REST, GraphQL, gRPC, Kafka, webhook patterns); eleven collector defects root-caused and fixed TDD-first; both Spring Data JPA repos now reach band HIGH / VERIFIED 92% on every element edge with runtime CORROBORATED."
tags: [lineage, measurement, oss, sca, runtime, confidence]
timestamp: 2026-08-14T20:00:00Z
---

# OSS Java Lineage Measurement

Ten popular open-source Java repositories — two each for REST/JPA, GraphQL, gRPC/proto,
Kafka, and webhook patterns, every checkout pinned to an exact revision — were driven
through `collect-checkout --runtime-verification` with the `java-spring-data-jpa-v1`
pack. The full method, per-repo matrix, defect list, and verification evidence live in
the repo doc this note distills.

**Outcome.** Eleven defects were found and fixed test-first: three SCA (to-many
`@JoinColumn` misvalidation blocking real petclinic, anonymous `CREATE INDEX` treated as
malformed SQL, stale 15/10/5 oracles), six runtime (harness compile scope missing
transitive superclasses, stub surface too small for any real repo, `JpaRepository`-only
recognition, parent-interface injection invisible, recorder reporting Java field names
instead of physical columns, misleading `execution-failed` reasons with truncated
diagnostics), one consolidation (catalog resolver rebinding observations across systems
by bare table name — a false-corroboration risk), and one acquisition
(`SOURCE_VALIDATION_FAILED` hiding its bounded reason).

**Verified band on real repositories is now reached**: spring-petclinic (8/8) and
spring-petclinic-rest (10/10) element edges consolidate at band HIGH / display
VERIFIED 92% with SCA+RUNTIME provenance and ELEMENT corroboration — closing, for the
pack's declared population, the "one-signal static lineage on real repositories" gap
recorded in [Prototype Collection Gap Assessment](/notes/prototype-collection-gap-assessment.md).
The other eight repos remain honest INTEGRATION_REQUIRED/INVALID refusals with
diagnosable reasons; nested-module discovery, Kotlin DSL, multi-module reconciliation,
oversized-asset pinning, symlink disposition, and non-JPA data planes are recorded as
report-only proposals.

## Related

* [Prototype Collection Gap Assessment](/notes/prototype-collection-gap-assessment.md) - the gap this measurement closes for the Spring Data JPA population
* [Lineage Collector](/projects/lineage-collector.md) - the platform under measurement
* [Lineage Platform Remaining Goals](/projects/remaining-goals.md) - the goal ledger this evidence feeds

## Citations

1. [oss-java-lineage-measurement.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/oss-java-lineage-measurement.md)
