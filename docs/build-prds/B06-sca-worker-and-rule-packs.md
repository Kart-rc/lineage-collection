# B06 — SCA worker and rule packs

Normative requirements: [L04 SCA](../component-prds/04-sca-engine.md) and
[L01 resolver](../component-prds/01-urn-and-resolver-library.md).

## Ownership

Track C owns the sandboxed worker, parser adapters, rule packs, residue contract, fixture corpus and
resource bounds. Language/platform owners approve supported syntax and framework conventions.

## Boundary

Read an exact source artifact and pinned resolver/ruleset, perform deterministic static analysis,
and write checksummed SCA evidence plus explicit unsupported/residue scope. No publication occurs.

## Contracts

The task input is a versioned S3 reference with repository/artifact/scope and determinant versions.
Output references strict evidence containing exact citations, transforms, mechanisms and coverage.

## State and failure model

One immutable result exists per determinant set. Parse failures, timeouts, unsupported syntax and
resource bounds are recorded per scope; crashes redrive. Partial output cannot claim complete scope.

## Data ownership

B06 owns parser/rule artifacts, SCA evidence, citations, residue and parser coverage. Source remains
in the source artifact boundary and is not retained in logs.

## Infrastructure bill of materials

Non-root ECS/Fargate task definition, immutable image/SBOM, read-only root filesystem, ephemeral
scratch, private subnets/restricted egress, task queues, S3 evidence access, KMS, logs and alarms.

## Local adapter

The Python AST analyzer runs fixture repositories in-process with the same resolver pins, schemas,
ruleset identity and deterministic output oracle.

## Security and privacy

Treat repositories as hostile: no credential mounts, constrained CPU/memory/time/disk, restricted
network, archive/path validation, log redaction and image scanning/signing.

## SLOs

Determinism, termination and bounded fan-out are hard gates. Language-specific throughput targets
require corpus measurements and cannot be inferred from the seeded Python fixture.

## Observability

Emit parser/rule version, scope counts, exact/unsupported/residue rates, duration/resource use,
timeouts and evidence checksum by correlation ID.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B06-AC-001 | L04 deterministic bounded analysis | Positive/negative/parser-error/hostile fixtures terminate within configured bounds and replay byte-identically with complete scope accounting | Every PR corpus gate |

## Deployment and rollback

Build signed images and rule bundles, canary against the full corpus, then advance task-definition
and ruleset aliases. Rollback pins the prior image/ruleset; old evidence remains addressable.

## Dependencies

B01 contracts, B02 resolver, B03 artifacts, B05 stage execution. Repository/framework conventions
and supported-language priority are CTX seams.

## Definition of Ready

Language/parser matrix, positive/negative corpus, resource bounds, resolver/ruleset versions and
unsupported behavior are approved.

## Definition of Done

Every supported cell has fixtures; hostile inputs are bounded; output is deterministic and strict;
task IAM/network/alarms and `B06-AC-001` evidence exist.
