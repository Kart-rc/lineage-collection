---
type: Reference
title: "Throughline Prototype: Lineage Collection Deep Dive"
description: "Distinguished-engineer feasibility proposal grounding every collection claim in primary sources, restructuring extraction into a three-tier ladder, and naming identity resolution as the top technical risk."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Deep Dive - Lineage Collection Proposal.dc.html
tags: [prototype, throughline, feasibility, llm-extraction, identity-resolution, confidence]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Lineage Collection Deep Dive

The engineering deep dive (v1.0, for distinguished-engineer review) behind PRD 1: every claim about
third-party tooling is cited to one of eleven primary sources, and every known failure mode is stated
up front. Position: the two-plane architecture (build-time predicted superset reconciled against
runtime-observed evidence, per-edge confidence) is right and unavailable off the shelf, but two PRD 1
components are infeasible as specified — a Dask OpenLineage collector (does not exist anywhere) and
LLM self-reported confidence (empirically near-random, AUROC 0.524) — and the single highest risk is
identity resolution across signals, not any collector.

Key specifications and evidence:

- **Three-tier build-time ladder.** Tier 1: deterministic parsers wherever grammar has semantics
  (SQLGlot-class SQL parsing — benchmarked most accurate by DataHub on ~9K real statements — dbt
  manifests, OpenAPI/protobuf). Tier 2: tree-sitter as candidate-finder and context-slicer (100+
  grammars, error-tolerant, incremental) locating anchors like Kafka annotations and S3 path literals.
  Tier 3: LLM for the semantic residue only (column mapping, transforms, guards in dynamic SQL/UDFs/
  notebooks) — schema-validated JSON, cached by code hash, quarantined at Inferred (score ≤ 64) until
  runtime corroborates. Never send well-formed SQL to an LLM.
- **LLM evidence constraint** — published schema-lineage extraction: GPT-4.1 scores 0.418 zero-shot,
  0.673 one-shot, 0.767 with chain-of-thought (SLiCE metric), degrading with script complexity; the
  promotion gate, not model confidence, is the safety mechanism.
- **vs CodeQL** — not a substitute (language breadth ~10 vs 100+, buildable-snapshot requirement,
  minutes-per-PR latency, licensing, notebooks/DAG configs outside its model) but a credible later
  precision upgrade on hot JVM paths. Decisive point: both are repo-scoped; no static analyzer crosses
  the Kafka/S3/service boundary — the architectural argument for the runtime plane.
- **Runtime collectors with failure modes attached** — Spark OL listener (Catalyst-based; RDD column
  extraction disabled, opaque UDFs, JDBC name-only, MLlib unsupported; facet omission is silent so
  coverage must be measured); warehouse-native federation (Unity Catalog, Snowflake ACCESS_HISTORY,
  Dataplex — the most gap-ridden); Airflow provider and dbt-ol (mostly dataset-level); OTel scoped to
  interactions and recency (traces are trees, lineage is a DAG); Dask honestly deferred to a Phase-3
  estate-share decision.
- **Three convergence joins** over canonical URNs: static column edge × Spark columnLineage facet
  (column promotion to Verified), static Kafka-consume edge × OTel messaging spans (HOT path,
  recency), static REST-call edge × OTel client spans (interaction confirmation). Non-matches are
  information: never-observed static edges stay Inferred and feed Curator questions; unpredicted
  runtime edges flag drift and measure extractor recall.
- **Identity resolution as P0** — canonical URN scheme with code-reviewed per-system normalizers,
  service catalog as identity backbone, deterministic match first with fuzzy candidates quarantined,
  and a PI-1 spike with numeric exit gates (GO ≥ 95% precision / ≥ 90% recall; PIVOT = manual mapping
  layer; STOP = per-system lineage without cross-signal merge).
- **Confidence engine** — weights {static 30, llm 18, spark 30, dask 26, otel 22}; recency multipliers
  (fresh 1d = 1.0, aging 7d = 0.82, never = 0) on runtime terms only; static-only capped at 64;
  Verified requires score ≥ 85 plus at least one runtime signal. Amendments: LLM self-confidence
  removed as an input, a calibration plan fitting weights to labeled outcomes per signal per stack,
  and an explicit conflict-resolution matrix (runtime wins for what happened, static for expression
  text, registry authoritative on schema/type).
- **Alternatives, honestly** — warehouse-native only, metadata platforms (DataHub/Marquez/Atlan/
  Collibra), pure static (SQLMesh-style), pure runtime, Spline — each credited then shown
  insufficient against the test: can it say in the PR what a change breaks, including unrun paths,
  and how much to trust that answer? Novelty budget is spent only on reconciliation and the tier-3
  residue; everything else is deliberately unoriginal.
- **Open problems stated plainly** — cold/seasonal paths ("low confidence ≠ low risk"; the tail is
  human-verified, system-prompted), sub-column semantics beyond the OL facet, LLM cost/model drift
  (versioned by model+prompt, standing eval set), the coverage tail (Flink, Kafka Streams, stored
  procedures, BI tools), unwritten security/PII posture, and an unvalidated graph-store sizing
  (~1M nodes, ~3M edges, ~500K events/day at 10K services; Phase 0 doubles as the storage bake-off).

## Related

* [Throughline Prototype: Architecture Flow for Tech Leaders](/references/prototype-architecture-flow-tech-leaders.md) - the leadership-level companion brief of this same design.
* [Throughline Prototype: PRD 1 Lineage Collection](/references/prototype-prd-1-lineage-collection.md) - the PRD whose components this document feasibility-grades and amends.
* [Static Code Analysis Engine](/references/sca-engine.md) - the implemented build-time plane realizing the parser/tree-sitter/LLM ladder.
* [Consolidation, URN Merge, and Confidence](/references/consolidation-and-confidence.md) - the implemented reconciliation and scoring layer where this document says the novelty budget lives.
* [LLM Inference Gateway](/references/llm-inference-gateway.md) - the implemented tier-3 residue path with the caching and containment guardrails specified here.

## Citations

1. [Deep Dive - Lineage Collection Proposal.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Deep Dive - Lineage Collection Proposal.dc.html)
