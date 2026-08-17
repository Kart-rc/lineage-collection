---
type: Reference
title: "Throughline Prototype: Lineage Directions"
description: "Six design directions on one universal graph: three visual languages for the lineage canvas and three impact-analysis treatments of a schema-change scenario."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Throughline - Lineage Directions.dc.html
tags: [prototype, throughline, design-directions, topology, impact]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Lineage Directions

An early design-exploration document offering six directions to react to, all running on one
universal graph spanning services, external sources, a stream platform, S3 landing, analytical
apps, S3 files, and file transfers — with many-to-many, multi-pattern flows (data may originate
from a service, land directly from a third party, skip the stream, or flow back into an
operational system).

Topology model: every node is one of four roles, shape-encoded — Service (circle, an app that
originates or receives data including reverse-ETL targets), Source (ring, an external third-party
feed), Job (diamond, a process that reads and writes datasets — dbt/Spark/SFTP runs), Dataset
(square, data at rest with a schema — topics, S3 tables, files). Columns encode flow stage (hops
from a source), not system type. Edges are inferred from each job run's input/output facets and
publish bindings — never hand-drawn.

Direction set A — the lineage canvas:

- A1 Airy card flow: owner/freshness/edge cards with expandable datasets and columns.
- A2 Dense technical DAG: orthogonal layout with live collector status (nodes, edges, depth,
  last-ingest).
- A3 Element-level inspector: column lineage with classification (PII/RESTRICTED), types, and
  named transforms (e.g. lower(trim($)), sha256($)).
- A4 Service-to-endpoint drill-down: lineage anchors to the endpoint, not the service, so a field
  change flags only consumers of that field while the service's other endpoints stay untouched.
- A5 Stream-platform drill-down: producer -> topic -> processor -> state store -> cache -> sink
  connector, all reducing to Jobs + Datasets so lineage works identically; stream semantics
  (partitions, key, registry subject, Avro version, retention, delivery guarantee, consumer-group
  lag) are first-class, and a schema change is gated by the registry compatibility check before
  impact flows downstream.

Direction set B — impact analysis of a customer_raw.email type change (string -> varchar(320)):

- B1 Blast radius on canvas: changed source, impacted, and unaffected nodes distinguished, with
  downstream-only tracing.
- B2 Change simulation report: pick asset/column/change type, run analysis, get counts by severity
  (8 affected: 2 breaking, 4 warning, 2 info) with owners per finding.
- B3 Schema diff and consumers: v14 -> v15 before/after with changed/added/removed markers and a
  per-consumer verdict list (contract-length break, fixed-width export break, join-cast warning,
  regenerated-feature warning).

The document seeds vocabulary the later PRDs formalize: severity tiers, simulation-before-commit,
endpoint-level anchoring, and the job/dataset duality.

## Related

* [Throughline Prototype: PRD 2 Impact Analysis](/references/prototype-prd-2-impact-analysis.md) - formalizes the severity and simulation ideas explored in direction set B
* [Throughline Prototype: PRD 3 UI Representation](/references/prototype-prd-3-ui-representation.md) - the PRD that settled the canvas questions these directions posed
* [Throughline Prototype: Final Product Document](/references/prototype-throughline-final.md) - the shipped prototype these explorations converged into
* [Throughline Prototype: Deep Dive Lineage Collection](/references/prototype-deep-dive-lineage-collection.md) - elaborates the collector-inferred edges this document assumes

## Citations

1. [Throughline - Lineage Directions.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Throughline - Lineage Directions.dc.html)
