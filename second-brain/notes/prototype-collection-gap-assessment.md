---
type: Note
title: Prototype Collection Gap Assessment
description: "Assessment (2026-08-14): the implementation is vocabulary- and mechanism-complete against the Throughline collection expectation, but real repositories still get one-signal static lineage, so the Verified band is unreachable outside the demo path."
tags: [lineage, prototype, gap-analysis, confidence, runtime]
timestamp: 2026-08-14T13:00:00Z
---

# Prototype Collection Gap Assessment

Question assessed: does the current implementation sufficiently address the prototype
lineage-collection expectation (`Throughline - Agentic.dc.html`)?

**Verdict: yes at the level the repo defines sufficiency; not yet for the prototype's four-signal
trust story on real repositories.** The scorecard is real — 18/18 dimensions on fixtures, 9/9 on
the real microservices checkout, expectation drift breaks the build — and the static plane,
consolidation bands, CI/CD currency, path disposition, and interactions plane are genuinely
implemented. But on a real repository today collection is effectively one-signal (static): runtime
is `NOT_PROVIDED`, the LLM tier never calls a model, so no real edge can honestly reach the
Verified band — the band the prototype declares safe for automated impact gating.

Gaps, each deliberately recorded rather than silently missing:

1. LLM signal interface-only (CTX-09, G-L05-1); tier-3 residue extraction unproven.
2. Runtime signals are local contracts, not installed collectors (CTX-10..12); runtime execution is a library-level opt-in not exposed by CLI or the collections API; OTel span-to-interaction enrichment designed, not built.
3. Verified band demonstrated only on purpose-built fixtures.
4. Liveness reads COLD/UNOBSERVED, not traffic-derived HOT/WARM/DEAD?.
5. Outbound interaction extraction covers @FeignClient only.
6. Identity-resolution P0 spike (GO >= 95% precision / >= 90% recall) never run against a real estate catalog (CTX-01..04); scale claim of 2,000+ services unmeasured.
7. Analyzer breadth is Python + Java/Spring; Spark/Dask cells deferred.

Leverage-ordered closure sequence: (1) expose runtime verification on the product path (CLI +
`POST /api/collections` flag); (2) build OTel enrichment and extend outbound extraction to
RestTemplate/WebClient; (3) stand up the guardrailed tier-3 LLM path against an approved model;
(4) run the identity-resolution spike on a real catalog snapshot. Steps 3-4 are blocked on CTX
owners, consistent with [Lineage Platform Remaining Goals](/projects/remaining-goals.md).

## Related

* [Throughline Prototype: Agentic Product Document](/references/prototype-throughline-agentic.md) - the expectation source this assessment measures against
* [Throughline Prototype: PRD 1 Lineage Collection](/references/prototype-prd-1-lineage-collection.md) - defines the four-signal collection thesis whose gap this note quantifies
* [Throughline Prototype: Lineage Collection Deep Dive](/references/prototype-deep-dive-lineage-collection.md) - supplies the tier ladder, confidence weights, and identity-resolution gate cited here
* [Implementation and Evidence Coverage](/references/prototype-coverage.md) - the evidence matrix grounding every "met" claim
* [Lineage Platform Remaining Goals](/projects/remaining-goals.md) - carries the owner-blocked prerequisites for closing gaps 1-4

## Citations

1. [Throughline - Agentic.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Throughline - Agentic.dc.html)
2. [prototype-coverage.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/prototype-coverage.md)
3. [verify_prototype_alignment.py](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/scripts/verify_prototype_alignment.py)
