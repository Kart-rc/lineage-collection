---
type: Reference
title: "Throughline Prototype: Agentic Product Document"
description: The normative interactive prototype adding agents — a Confidence Curator, a Pre-merge Impact Reviewer, lineage-correlated alerting, and grounded Ask Throughline — on top of the four-lens product.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Throughline - Agentic.dc.html
tags: [prototype, throughline, agents, confidence, expectation]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Agentic Product Document

The richest interactive Throughline prototype and the platform's normative expectation source: its
vocabulary is machine-extracted by scripts/extract_prototype_expectation.py into
docs/architecture/prototype-expectation.json, and tests assert the implemented platform covers
every extracted term — so drift from this document breaks the build. The same content ships as the
self-contained bundled rendering Throughline.html (packaging, not a separate document).

It models the full four-lens product — Lineage, Interactions, Impact, Provenance — with altitude
navigation (progressive disclosure L0 services -> L1 inputs/outputs -> L2 dataset fields -> L3
field lineage), roll-up domain edges, scoped loading, and search-first anchoring on an entity's
k-hop neighbourhood. Dataset kinds: Kafka topic, S3 landing, S3 file, cache, OLTP datastore,
search index. Interaction channels: REST sync request, GraphQL query, gRPC sync RPC, async event —
kept off the lineage graph because calls move requests, not data. Nodes carry DQ health scores,
contract version/SLA/owner/bound consumers, and per-field confidence with band, last-seen, and
contributing signals; field views show UP/VIA/DOWN derivation with transforms.

Provenance is framed as the live trust layer: signals that agree make an edge Verified, code-only
edges stay Predicted, and low-confidence edges draw dashed amber on the canvas. A confidence score
combines three weighted factors — signal agreement, runtime recency, and path execution frequency —
and a verification lifecycle states that pre-deploy lineage is predicted from code while runtime
confirms it after ship. Every PR re-derives lineage for changed code, diffs it against baseline,
and re-scores affected edges before the gate.

What makes this document "agentic" is its four autonomous surfaces:

- Confidence Curator (autonomous, audited): hunts the lowest-confidence edges consumers actually
  rely on, verifies them against runtime, and — when a path cannot be observed — asks the owning
  team one targeted question (keep or retire the edge).
- Pre-merge Impact Reviewer: reviews a PR's lineage diff in seconds, reports the blast radius
  (apps, consumers, breaking edges), and collects required acknowledgements from breaking-edge
  consumers before the gate.
- Lineage-correlated alerting: downstream symptoms fold into their root cause — one incident, not
  fifteen — and only the owning team is paged.
- Ask Throughline: a conversational surface over the graph for impact, root cause, ownership, and
  trust questions; every answer cites the nodes and edges it reasoned over and links to the lens
  where the evidence lives.

Impact simulation lets a producer change a service's output schema and watch the blast radius
update live — impacted services, datasets, breaking counts, schema diff, per-consumer severity —
then notify each affected team with its own impact summary.

## Related

* [Implementation and Evidence Coverage](/references/prototype-coverage.md) - records how the implemented platform scores against this document's extracted expectation
* [Throughline Prototype: Final Product Document](/references/prototype-throughline-final.md) - the same product prototype without the agentic surfaces, showing what the agents added
* [Throughline Prototype: PRD 1 Lineage Collection](/references/prototype-prd-1-lineage-collection.md) - specifies the collectors and confidence model this prototype renders
* [Throughline Prototype: Agentic Experiences](/references/prototype-agentic-experiences.md) - the companion document cataloguing these agent experiences
* [LLM Inference Gateway](/references/llm-inference-gateway.md) - the implemented component underpinning the platform's inference-driven surfaces
* [Lineage Collector](/projects/lineage-collector.md) - the implemented platform tested against this expectation source

## Citations

1. [Throughline - Agentic.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Throughline - Agentic.dc.html)
2. [Throughline.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Throughline.html)
