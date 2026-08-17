---
type: Reference
title: "Throughline Prototype: Final Product Document"
description: The final non-agentic interactive prototype demonstrating the four lenses, altitude navigation, live impact simulation, and the provenance trust layer end to end.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Throughline - Final.dc.html
tags: [prototype, throughline, ui, provenance, simulation]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Final Product Document

The finished interactive Throughline prototype (a working single-page app over sample revenue-
domain data) demonstrating the complete product model without the agentic surfaces. It is the
closest expression of PRD 3's design realized as clickable software, and the base the Agentic
document extends.

What it demonstrates:

- Four lenses over one graph — Lineage, Interactions, Impact, Provenance — sharing scope chips,
  a stat strip (domains/services/datasets/jobs), and a Domains/Apps/Services disclosure toggle.
- Altitude navigation as progressive disclosure: L0 abstracted service-to-service flow, L1 a
  service's datasets/topics/caches, L2 element-level dataset schema, L3 per-field upstream/
  downstream lineage. Scaling rests on four moves: hierarchy (Org > Domain > Service > Dataset >
  Column), roll-up domain-to-domain edges with counts, scoped loading of only a domain's assets
  plus boundary neighbours, and search-first k-hop anchoring — never the global graph.
- A service node is a deployable unit, not just an API: endpoints, stream consumers/producers,
  and scheduled batch jobs, each a typed code path with its own channel (batch, API, streaming all
  first-class). Sync REST/GraphQL/gRPC and async events live in the separate Interactions lens
  because calls move requests, not data. Dataset types: Kafka topic, S3 landing, S3 file, cache,
  OLTP datastore, search index.
- Provenance as the live trust layer: every edge carries a confidence score recomputed as PRs
  merge and runs report; agreeing signals make an edge Verified while code-only edges stay
  Predicted, and low-confidence edges draw dashed amber on the canvas. The score combines signal
  agreement, runtime recency, and path execution frequency; a verification lifecycle explains
  that pre-deploy lineage is predicted and runtime confirms after ship; a CI/CD lineage diff
  re-derives, diffs, and re-scores edges on every PR before the gate.
- Baseline-versus-generated reconciliation is argued explicitly: static alone over-reports paths
  that never run, runtime alone misses rarely-fired or just-shipped paths — keeping both and
  scoring agreement is what makes impact trustworthy.
- Impact simulation: a producer changes a service's output schema and the blast radius updates
  live — impacted services, datasets, breaking counts, schema version diff, and per-consumer
  severity — without touching production. Datasets carry DQ health scores, quality checks, and
  data contracts (SLA, schema version, owner, bound consumers).

## Related

* [Throughline Prototype: Agentic Product Document](/references/prototype-throughline-agentic.md) - the extension of this prototype that adds agents, alert correlation, and Ask Throughline
* [Throughline Prototype: PRD 3 UI Representation](/references/prototype-prd-3-ui-representation.md) - the PRD whose lens and altitude model this prototype realizes
* [Throughline Prototype: PRD 1 Lineage Collection](/references/prototype-prd-1-lineage-collection.md) - source of the collector pipeline and confidence anatomy shown in the Provenance lens
* [Throughline Prototype: Worked Example](/references/prototype-worked-example.md) - a narrative walkthrough of the flows this prototype makes interactive

## Citations

1. [Throughline - Final.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Throughline - Final.dc.html)
