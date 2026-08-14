---
type: Reference
title: B06 SCA Worker and Rule Packs
description: A sandboxed, deterministic static-code-analysis worker that reads an exact source artifact under pinned resolver/ruleset determinants and writes checksummed SCA evidence plus explicit unsupported/residue scope.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B06-sca-worker-and-rule-packs.md
tags: [lineage, build-prd, sca, static-analysis, security]
timestamp: 2026-08-14T11:30:00Z
---

# B06 SCA Worker and Rule Packs

B06 (Track C) delivers the sandboxed SCA worker, parser adapters, rule packs, the residue contract,
fixture corpus and resource bounds. Its boundary: read an exact source artifact and pinned
resolver/ruleset, perform deterministic static analysis, and write checksummed SCA evidence plus
explicit unsupported/residue scope. No publication occurs here.

## Key contracts and invariants

- Task input is a versioned S3 reference with repository/artifact/scope and determinant versions;
  output references strict evidence with exact citations, transforms, mechanisms and coverage.
- One immutable result per determinant set; replays must be byte-identical.
- Parse failures, timeouts, unsupported syntax and resource-bound hits are recorded *per scope*;
  crashes redrive; partial output cannot claim complete scope.
- Residue — what static analysis could not decide — is an explicit output, feeding B07's LLM
  eligibility rather than being silently dropped.
- B06-AC-001: positive/negative/parser-error/hostile fixtures terminate within configured bounds
  and replay byte-identically with complete scope accounting (every-PR corpus gate).

## Sandbox posture

Repositories are treated as hostile: non-root ECS/Fargate task, immutable signed image with SBOM,
read-only root filesystem, ephemeral scratch, private subnets with restricted egress, no credential
mounts, constrained CPU/memory/time/disk, archive/path validation and log redaction. Source is
never retained in logs. The local adapter runs the Python AST analyzer in-process against fixture
repositories with the same resolver pins, schemas and ruleset identity.

## Rollback and seams

Signed images and rule bundles canary against the full corpus before task-definition and ruleset
aliases advance; rollback pins the prior image/ruleset while old evidence remains addressable.
Depends on B01 contracts, B02 resolver, B03 artifacts and B05 stage execution. Repository/framework
conventions and supported-language priority are CTX seams; language-specific throughput cannot be
inferred from the seeded Python fixture.

## Related

* [SCA Engine](/references/sca-engine.md) - normative L04 static-analysis requirements B06 implements
* [B02 Catalog Snapshot and Resolver Packages](/references/b02-catalog-and-resolver.md) - upstream resolver whose pinned version is an analysis determinant
* [B05 Classifier and Workflow Orchestrator](/references/b05-classifier-and-orchestrator.md) - upstream orchestrator that schedules SCA stage execution
* [B07 LLM Inference Gateway](/references/b07-llm-inference-gateway.md) - downstream consumer of B06's explicit residue scope
* [URN and Resolver Library](/references/urn-and-resolver-library.md) - normative L01 resolver requirements binding B06's identity pins

## Citations

1. [B06-sca-worker-and-rule-packs.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B06-sca-worker-and-rule-packs.md)
