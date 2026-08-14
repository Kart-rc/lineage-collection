# OSS Java lineage measurement — issues and fixes (2026-08-14)

Deterministic collection (`collect-checkout --runtime-verification`, pack
`java-spring-data-jpa-v1`, ruleset `spring-data-rules-v1`, profile `postgres`) was run
against ten popular open-source Java repositories, two per integration pattern, each at a
pinned revision. Every failure was root-caused; the highest-leverage collector defects
were fixed test-first on this branch, and the corrected edge maps were re-verified through
the product path with confidence projection.

## Measured matrix

| Pattern | Repository | Revision | Baseline | Post-fix |
|---|---|---|---|---|
| REST/JPA | spring-projects/spring-petclinic | `88e37c15` | INTEGRATION_REQUIRED (`unmapped-entity-column`), 23 edges, runtime `execution-failed` | **ACCEPTED · COMPLETE · 23 edges · runtime CORROBORATED · 8/8 element edges band HIGH (VERIFIED 92%)** |
| REST/JPA | spring-petclinic/spring-petclinic-rest | `698bd832` | INTEGRATION_REQUIRED (`malformed-sql`), 44 edges, runtime `execution-failed` | **ACCEPTED · COMPLETE · 44 edges · runtime CORROBORATED · 10/10 element edges band HIGH (VERIFIED 92%)** |
| GraphQL | spring-petclinic/spring-petclinic-graphql | `0aa4212f` | INTEGRATION_REQUIRED (`missing-boot-evidence`, …) | unchanged — app lives in `backend/`, outside the pack's root-build scope (see proposals) |
| GraphQL | Netflix/dgs-examples-java | `ad5547b9` | INTEGRATION_REQUIRED (`missing-jpa-dependency`, …) | unchanged — genuinely no JPA data plane; runtime reason now `no-groundable-edges` |
| gRPC | grpc-ecosystem/grpc-spring | `b83afe68` | INTEGRATION_REQUIRED (`dynamic-framework-evidence`, …) | unchanged — multi-module framework repo, dynamic Gradle versions |
| gRPC | LogNet/grpc-spring-boot-starter | `43b17c7d` | INVALID (`SOURCE_VALIDATION_FAILED`, no reason) | still fail-closed, but the CLI now reports the bounded reason: an 8.3 MB `images/demo.gif` exceeds the 4 MiB per-file limit |
| Kafka | confluentinc/kafka-streams-examples | `7b8c2520` | INTEGRATION_REQUIRED (`unknown-framework`, …) | unchanged — Kafka Streams, not Spring Data JPA |
| Kafka | spring-cloud/spring-cloud-stream-samples | `2ff11688` | INVALID (`SOURCE_VALIDATION_FAILED`, no reason) | still fail-closed, with the bounded reason surfaced: tracked `.mvn` symlinks are forbidden |
| Webhook | spring-cloud/spring-cloud-config | `2fc6367e` | INTEGRATION_REQUIRED (`missing-boot-evidence`, …) | unchanged — multi-module Maven, no root boot/JPA cell |
| Webhook | spinnaker/echo | `59f7796b` | INTEGRATION_REQUIRED (`malformed-build-file`, …) | unchanged — dynamic-versioned multi-module Gradle |

The eight INTEGRATION_REQUIRED / INVALID outcomes are honest refusals under the pack's
declared scope; their reasons are now diagnosable end to end. The two Spring Data JPA
repositories — the population the pack claims — both reach the platform's highest
achievable confidence for two-signal evidence: band HIGH, display VERIFIED 92%,
provenance SCA+RUNTIME, corroboration ELEMENT.

## Defects found and fixed (all TDD'd on this branch)

### SCA process

1. **To-many `@JoinColumn` misvalidated against the owning table** — a unidirectional
   `@OneToMany @JoinColumn(name="owner_id")` names the foreign key on the TARGET table,
   but validation looked it up in the owning entity's own table and quarantined real
   petclinic mappings as `unmapped-entity-column`, blocking completion. Fields now carry
   a `toMany` fact; a to-many join column neither claims nor contradicts an own-table
   column. (`java_spring_sca.py`)
2. **Anonymous PostgreSQL `CREATE INDEX ON t (c)` classified `malformed-sql`** — the
   optional-name form is valid DDL the server names itself; petclinic-rest's schema was
   blocked by it. The closed index grammar and the sqlglot validation now accept an
   absent index name as ignorable inventory. (`java_spring_sca.py`)
3. **Petclinic oracle drift** — element-grounding legitimately emits an element-scoped
   edge alongside each proven dataset-scope claim; the opt-in oracles still pinned
   15/10/5. All oracles (product-flow test, per-edge acceptance oracle — now carrying an
   explicit `element` dimension — acceptance supervisor, README) were reconciled to
   23/18/5, with the 8 element rows spelled out.

### Runtime process

4. **Harness compile scope missed transitive project types** — entities extending
   `Person`/`BaseEntity` (`@MappedSuperclass`, other packages, same-package references
   with no import) failed `javac` for every real repository. Selection now closes over
   referenced project types to a fixpoint, comments stripped. (`java_runtime_stage.py`)
5. **Framework stub surface too small** — the compile-isolation stubs covered only the
   flat microservices fixture. Added the JPA relationship set (`MappedSuperclass`,
   `FetchType`, `CascadeType`, `JoinColumn`, `JoinTable`, `ManyToMany`, `OneToMany`,
   `OrderBy`, `UniqueConstraint`, …), Bean Validation, `jakarta.xml.bind`, Spring MVC
   model/binding/redirect types, data-commons paging, `ToStringCreator`, `StringUtils`,
   `Assert`, DAO exceptions, `@Profile`, `@Param`. (`java_runtime_verification.py`)
6. **Only `JpaRepository` was recognized** — petclinic's `VetRepository extends
   Repository<Vet, Integer>` and every Crud/ListCrud/PagingAndSorting base are now
   instrumentable.
7. **Parent-interface injection was invisible** — petclinic-rest declares
   `SpringDataOwnerRepository extends OwnerRepository, Repository<Owner, Integer>` and
   injects `OwnerRepository`. Repositories now record their parent interfaces; injection
   sites typed against a parent resolve to the Spring Data repository (ambiguous parents
   resolve to nothing), and the generated test looks constructors up by the declared type
   while proxying the Spring Data type.
8. **Recorder reported Java field names, not physical columns** — an SCA element edge
   names `pet_id` (the association's `@JoinColumn`), never the field `pet`. The recorder
   now reports `@Column`/`@JoinColumn` names (to-one only) and walks `@MappedSuperclass`
   ancestors for inherited fields.
9. **Misleading runtime reasons and undebuggable diagnostics** — zero groundable edges
   now reports `no-groundable-edges` instead of `execution-failed`, and a
   `CalledProcessError` diagnostic carries a bounded tail of the compiler's stderr.
   (`orchestration.py`)

### Consolidation

10. **Catalog resolver rebound observations across systems** — `postgres://petclinic-rest/owners`
    resolved by bare table name to the demo catalog's `petclinic:owners`, so the edge's
    own observation silently never corroborated (and a same-named edge in another system
    could have corroborated falsely). A relational wire identifier's authority is the
    system claim; a catalog answer naming a different system is now refused in favour of
    the wire. Host-shaped authorities (`host:port`) still defer to the catalog.
    (`consolidation.py`)

### Acquisition

11. **`SOURCE_VALIDATION_FAILED` hid its reason** — a tracked symlink, an oversized blob,
    and a git stdout overflow were indistinguishable. The CLI now surfaces the
    validator's bounded, path-free message as `reason`. (`cli.py`)

## Verification

- Backend suite: 1249 passed (13 new tests, all watched red first).
- Opt-in real-checkout proofs at the pinned petclinic revision: product-flow (4 passed)
  and the full acceptance suite (38 passed) against the updated 23/18/5, 131-path,
  per-edge oracle.
- Product-path confidence projection (`scripts/measure_java_oss_lineage.py`):
  spring-petclinic 8/8 and spring-petclinic-rest 10/10 element edges at band HIGH /
  VERIFIED 92% with SCA+RUNTIME provenance — the ceiling for two-signal evidence; the
  Verified-band-on-real-repositories gap recorded in the second brain is closed for this
  population.

## Report-only proposals (out of this pass's scope)

- **Nested single-module discovery** — petclinic-graphql's app lives in `backend/`; the
  pack could reconcile a unique nested boot module when the root has no build cell.
- **Gradle Kotlin DSL** (`build.gradle.kts`) build-cell reconciliation (dgs-examples).
- **Multi-module reconciliation** for framework-style repos (grpc-spring,
  spring-cloud-config, echo).
- **Oversized-asset pinning** — pin large binary blobs by verified git object ID instead
  of refusing the whole checkout (grpc-spring-boot-starter's demo GIF).
- **Symlink disposition** — record tracked symlinks as skipped scope rather than
  refusing acquisition (spring-cloud-stream-samples), if the security posture allows.
- **Non-JPA data planes** — Kafka topics/gRPC services as first-class datasets; the
  kafka-binding SCA cell exists but is outside this pack.
