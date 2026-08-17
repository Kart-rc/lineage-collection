---
type: Reference
title: "Throughline Prototype: Onboarding Estimator"
description: "Interactive estimation model that quotes vertical-slice onboarding time from component archetypes, per-collector effort baselines with a connector-reuse learning curve, and criticality/complexity multipliers."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Onboarding Estimator.dc.html
tags: [prototype, throughline, estimation, onboarding, collectors]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Onboarding Estimator

An interactive estimation model (v0.9; unit: person-days → calendar weeks) giving the team a
repeatable, defensible way to quote onboarding time for end-to-end, element-level lineage on a
high-criticality vertical slice — bottom-up from components, not top-down from gut feel.

The method, in six steps: decompose the slice into **component archetypes** (event-publishing
service, sync service, Spark analytical app, Dask ML app, SFTP export, passive stores), map each
archetype to the collectors it needs (SCA+LLM, OTel, Spark, Dask, streaming — static for the shape,
runtime to confirm it), apply calibrated per-collector effort baselines, then adjust for criticality
and complexity.

The formula:

    Onboarding = Fixed setup
               + Σ per collector [ first_of_type + (n−1) × repeat ]
                 × Criticality × Code-health × Schema-volatility
               + Passive catalog registration

Key numbers and claims:

- **Fixed per-slice setup is 14 person-days** — environment, identity resolution, merge/confidence
  calibration, end-to-end sign-off.
- **The connector-reuse learning curve is the single biggest lever**: the first component of each
  collector type pays to stand up the connector; every later component of the same type costs a
  fraction (a 2nd streaming service is far cheaper than the 1st).
- **Calendar conversion**: person-days ÷ (pod size × 0.8 focus) × 1.15 for sequencing (identity and
  merge land before components). Estimates carry a ±25% band until the first real slice calibrates
  the model.
- **OTel decorators shrink streaming but don't eliminate it**: column-level runtime decorators carry
  per-service field mapping on the OTel line, but cannot join producer fields to consumer fields
  across the async boundary (consumer groups, replay, and late delivery break the single-trace
  assumption) — that edge is stitched on the event contract, and decorators only fire on executed
  paths, so a schema-registry join remains for completeness. The residual is the lightest runtime
  line, but dropping it silently assumes decorators stitch producer→consumer for free.
- The estimator ships **preloaded with the Orders → Partner Export slice** (two event-publishing
  services, one sync service, a Spark app, a Dask app, an SFTP export, plus passively registered
  S3/file/cache stores), itemized as a worked example, with sliders for composition, adjustment
  factors, and pod size.

## Related

* [Throughline Prototype: Worked Example](/references/prototype-worked-example.md) - traces the same Orders slice the estimator is preloaded with, end to end.
* [Throughline Prototype: PRD 1 Lineage Collection](/references/prototype-prd-1-lineage-collection.md) - defines the collectors whose effort baselines this model prices.
* [Throughline Prototype: Roadmap and LOE](/references/prototype-roadmap-and-loe.md) - the delivery plan this per-slice quoting tool feeds.
* [L13 Repository Classification and Eligibility PRD](/references/repository-classification.md) - the implemented platform's classification step echoes the archetype decomposition the estimate starts from.

## Citations

1. [Onboarding Estimator.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Onboarding Estimator.dc.html)
