---
type: Reference
title: B02 Catalog Snapshot and Resolver Packages
description: Cross-language resolver packages that deterministically resolve raw identifiers to canonical URNs against one explicitly pinned, signed catalog snapshot, with typed ambiguity quarantine.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B02-catalog-and-resolver.md
tags: [lineage, build-prd, resolver, urn, catalog]
timestamp: 2026-08-14T11:30:00Z
---

# B02 Catalog Snapshot and Resolver Packages

B02 delivers snapshot ingestion, URN normalization, ambiguity quarantine, resolver packages for
Python/JVM/Node/Go, and a cross-language golden corpus (Track A ownership). Its boundary: resolve
repository/path/native identifiers against **one explicitly pinned catalog snapshot** and emit a
canonical URN or a typed ambiguity/not-found result — it never silently queries a moving catalog.

## Key contracts

- Inputs bind raw identifier, environment, system hint and snapshot ID; outputs carry canonical
  URN, candidate list, decision reason and evidence reference.
- Snapshot manifests are signed and checksummed; one active pointer references immutable snapshots.
- Refresh failure may fall back to a *visible* last-good snapshot only within an approved age;
  past expiry the result is `STALE`/unavailable — never fresh success.
- B02-AC-001: repeated and cross-language resolution over the golden corpus must be byte-identical
  (every PR plus live canary).

## Rollback and seams

Canary a new package and snapshot, compare golden results, then conditionally advance the pointer.
Rollback restores the prior package alias and snapshot pointer without deleting immutable snapshots.
Source catalog records stay owned by the enterprise catalog; catalog export schema, vocabulary and
ownership mappings are explicit CTX seams. Resolver latency and refresh cadence remain externalized
until catalog and operations owners approve them. Snapshot signatures plus KMS permissions prevent
an analyzer from substituting identity truth; the local fixture snapshot honors the same pin,
checksum and ambiguity contract but is never presented as a production catalog integration.

## Related

* [URN and Resolver Library](/references/urn-and-resolver-library.md) - normative L01 resolver requirements B02 implements
* [Repository Classification](/references/repository-classification.md) - normative L13 classification requirements shaping B02
* [B01 Contracts, Schemas and Correlation](/references/b01-contracts-and-correlation.md) - upstream dependency for schemas and correlation
* [B06 SCA Worker and Rule Packs](/references/b06-sca-worker-and-rule-packs.md) - downstream consumer that pins B02 resolver versions as analysis determinants
* [Build-Ready Lineage Platform PRDs](/references/build-prd-overview.md) - places B02 in the foundations stage of the build order

## Citations

1. [B02-catalog-and-resolver.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B02-catalog-and-resolver.md)
