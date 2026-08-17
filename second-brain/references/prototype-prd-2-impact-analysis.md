---
type: Reference
title: "Throughline Prototype: PRD 2 Impact Analysis"
description: The prototype PRD specifying confidence-aware blast-radius computation in three modes — interactive, simulation, and CI/CD gate — with a change-type by usage severity matrix.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/PRD 2 - Impact Analysis.dc.html
tags: [prototype, throughline, impact-analysis, severity, pr-gate]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: PRD 2 Impact Analysis

Second Throughline PRD (v0.9 draft, owner Data Platform). Premise: a schema change in one service
can silently break a partner export three hops away and today that is discovered in production;
Throughline turns it into a pre-merge question — select a change, see which applications, datasets,
columns, and downstream contracts are affected, ranked breaking > warning > safe, with an owner on
each.

Key specifications:

- Three modes, one traversal engine: an interactive blast-radius on the graph, a simulation report
  before commit, and an automatic CI/CD gate on every PR.
- Goals: answer "what breaks if I change X?" at column granularity in seconds; classify every
  affected consumer by severity and route to its owner; run both pre-merge (simulation) and on
  merge (gate); never block on an unverified inferred edge. Non-goals v1: auto-generating fixes or
  migrations, data-quality/statistical drift prediction, cost/performance estimation.
- Impact is a directed downstream traversal from the changed element, pruned by what the change
  actually touches.
- Severity model: a matrix of change type crossed with how the consumer uses the element (read/
  passthrough vs transform/join key vs contract/export), yielding Breaking, Warning, or Safe.
- Confidence-aware two-tier blast radius: Confirmed impact is reached only via Verified/Probable
  edges, counts toward the CI/CD gate, and can block a merge; Possible impact is reached via at
  least one Inferred edge, is drawn dashed, surfaced for review, and never auto-blocks.
- Code-path coverage: a change affects every code path reading the element, not just the common
  one; execution class (hot/warm/cold vs unconfirmed) maps directly onto Confirmed vs Possible.
  Reports state branch coverage explicitly (e.g. 9/12 paths runtime-confirmed) because unconfirmed
  branches are exactly where undetected breaks hide.

## Related

* [Throughline Prototype: PRD 1 Lineage Collection](/references/prototype-prd-1-lineage-collection.md) - supplies the confidence bands and path-qualified edges this traversal rides on
* [Throughline Prototype: PRD 3 UI Representation](/references/prototype-prd-3-ui-representation.md) - the Impact lens surfaces this engine's blast radius interactively
* [Throughline Prototype: Lineage Directions](/references/prototype-throughline-lineage-directions.md) - explores three visual treatments of this PRD's schema-change scenario
* [B13 Query, Impact, and PR Gate](/references/b13-query-impact-and-pr-gate.md) - the implemented build PRD realizing this impact engine and gate
* [APIs, Review UI, and PR Gate](/references/apis-review-ui-and-pr-gate.md) - the platform component spec covering the gate surface this PRD proposed

## Citations

1. [PRD 2 - Impact Analysis.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/PRD 2 - Impact Analysis.dc.html)
