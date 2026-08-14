---
type: Reference
title: "Throughline Prototype: Worked Example"
description: "End-to-end walkthrough tracing one money column through two chained scenarios, showing a single schema change rippling six hops across two teams into a live ML feature."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Worked Example.dc.html
tags: [prototype, throughline, walkthrough, impact-analysis, orders-slice]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Worked Example

The suite's concrete walkthrough: one column traced end to end through all five platform
capabilities — collection → CI/CD → OTel runtime → impact → UI — across two real scenarios that
deliberately chain, so a single money column captured in Use Case A propagates into the ML features
of Use Case B and one schema change ripples the whole way.

**Use Case A — order capture → stream → S3 landing.** The Orders Service calls Customer and Payments
synchronously, publishes an `orders.events` Avro record to Kafka, and a streaming landing job writes
it to `s3://landing/orders_raw`. Each step is walked through the capability that captures it, with
code snippets and a note of which UI lens shows the result.

**Use Case B — S3 → analytical normalize → ML features.** A Spark job reads `orders_raw` plus
`customer_raw` and writes the normalized `fct_orders`; a Dask feature job derives `churn_features`,
which trains the Churn Model.

**The payoff — one change, six hops, two teams.** PR #482 changes `total_amount` from int to decimal
in the Orders Service. Because both use cases live in one graph, Throughline traces the change all
the way into a live ML feature — across an app boundary the Orders team cannot see from their own
repo. The impact chain distinguishes evidence quality exactly as the trust model prescribes:

- **CONFIRMED** — runtime-observed paths (the Orders landing flow and the hot Spark normalize) that
  can justify hard gating.
- **POSSIBLE** — the static-only feature edge (the Dask hop, where no runtime collector exists),
  which is flagged but never auto-blocked.

The example is the suite's proof-by-demonstration that the two-plane design, per-edge confidence,
and pre-merge impact question compose into a concrete developer experience, and it doubles as the
default scenario preloaded into the Onboarding Estimator.

## Related

* [Throughline Prototype: Onboarding Estimator](/references/prototype-onboarding-estimator.md) - prices onboarding for this same Orders slice as its preloaded default.
* [Throughline Prototype: PRD 2 Impact Analysis](/references/prototype-prd-2-impact-analysis.md) - specifies the blast-radius mechanics the PR #482 payoff demonstrates.
* [Throughline Prototype: Lineage Collection Deep Dive](/references/prototype-deep-dive-lineage-collection.md) - uses the same OrderService/orders.events/total_amount example to show what each extraction tier contributes.
* [L11 Query APIs, Review UI, and PR-Gate Experience PRD](/references/apis-review-ui-and-pr-gate.md) - the implemented PR-gate surface where a change like PR #482 is caught.

## Citations

1. [Worked Example.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Worked Example.dc.html)
