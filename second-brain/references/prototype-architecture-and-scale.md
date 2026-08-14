---
type: Reference
title: "Throughline Prototype: Architecture and Scale"
description: "Technical reference specifying the four-plane system architecture, process flows, REST+GraphQL API surface, schemas, and back-of-the-envelope sizing for tens of thousands of services."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Architecture & Scale.dc.html
tags: [prototype, throughline, architecture, scaling, api-design]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Architecture and Scale

The technical reference behind the three Throughline PRDs (v0.9 draft, Data Platform owner): services
and their APIs, end-to-end process flows, entity and API schemas, and a back-of-the-envelope sizing
exercise for tens of thousands of services across domains.

Core design decisions:

- **Four planes** — collection (signals in), graph core (reconcile, store, score), serving (impact and
  query APIs), and experience (the four-lens UI). Each box is an independently scalable service.
- **Backbone infrastructure** — event bus (Kafka), object store (S3), graph DB, search index, and
  blob/metrics storage.
- **Three core process flows** — each modeled as a pipeline of stages with a clear trigger, cadence,
  and output; the interactive page walks each stage with detail popovers.
- **API surface** — REST + GraphQL over the graph core. Ingestion is write-heavy and
  OpenLineage-compatible; query and impact endpoints are read-heavy and cached. Each endpoint card
  documents verb, path, consumers, and example input/output exchanges.
- **Schemas** — a canonical graph entity schema plus key API payloads shown as illustrative JSON;
  the wire format for ingestion is OpenLineage-compatible.
- **Interactive sizing model** — a slider scales the org (service count) and recomputes storage,
  event volume, and throughput figures from a small set of stated assumptions, rather than
  presenting fixed numbers.
- **Scaling strategy and bottlenecks** — a dedicated section enumerates pressure points at 10k+
  services with a mitigation per area (rendered as an area / pressure / fix table).

The document positions itself as the "build behind the PRDs": it is deliberately a reference, not a
pitch — sections are numbered 01–07 and cross-link to the Overview and Roadmap & LOE.

## Related

* [Throughline Prototype: PRD Index](/references/prototype-throughline-index.md) - the suite landing page that frames this as the technical reference for the three PRDs.
* [Throughline Prototype: Lineage Collection Deep Dive](/references/prototype-deep-dive-lineage-collection.md) - carries the concrete sizing figures (~1M nodes, ~3M edges, ~500K events/day at 10K services) this document's model motivates.
* [System Context and Architecture](/references/system-context.md) - the implemented platform's architecture that descends from this four-plane design.
* [Lineage Platform Target AWS Architecture](/references/lineage-platform-target.md) - the concrete AWS realization of the backbone (event bus, object store, graph store) sketched here.

## Citations

1. [Architecture & Scale.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Architecture & Scale.dc.html)
