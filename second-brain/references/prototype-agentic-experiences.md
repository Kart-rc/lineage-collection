---
type: Reference
title: "Throughline Prototype: Agentic Experiences"
description: "Exploration of an agentic layer that turns the lineage graph from a system of record into a system of action, with an autonomy ladder and data-driven build sequencing."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Agentic Experiences.dc.html
tags: [prototype, throughline, agents, autonomy, conversational-interface]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Agentic Experiences

An exploration document (interactive prototype page) arguing that the Throughline graph — which already
captures lineage, impact cones, PR diffs, attribute usage, and service interactions — should be turned
from a *system of record* into a *system of action* by a layer of agents. The agents review changes
before merge, watch the graph at runtime, assemble incident case files, and curate trust, each grounded
in evidence the platform already holds rather than in free-form model judgment.

Key vocabulary and decisions:

- **Autonomy ladder** — every agent sits on a three-rung scale: *Recommends (human decides)* →
  *Acts with approval* → *Autonomous, audited*. Autonomy is a property assigned per experience, not
  a global switch.
- **Ask Throughline** — a cross-cutting conversational interface over the graph. Natural-language
  questions ("What breaks if I drop currency_code?", "Why did yesterday's revenue dip?", "Who owns
  the true source of this field, and how fresh is it?") are answered with graph evidence cited back
  to specific nodes and edges. Every named agent is framed as this same reasoning loop given a
  standing job.
- **Sequencing principle** — "build order follows the data, not the ambition." Horizon 1 agents read
  signals the graph already captures and are characterized as prompt-and-plumbing work; later
  horizons require new signal (payload sampling, run history) or write access to external systems.
- **Scope guarantee** — each experience extends an existing PRD surface; none requires a new product.

The page is an interactive catalog: objectives with metrics, experience cards carrying a glyph,
autonomy rating, "uses" list, and the PRD surface each agent appears in (rendered from template
placeholders in the extraction).

## Related

* [Throughline Prototype: Agentic Throughline](/references/prototype-throughline-agentic.md) - the fuller agentic prototype document this exploration page distills and links into.
* [Throughline Prototype: Architecture Flow for Tech Leaders](/references/prototype-architecture-flow-tech-leaders.md) - the serve plane it extends already lists "UI & agents" and curation agents as consumers of the graph.
* [Throughline Prototype: Lineage Collection Deep Dive](/references/prototype-deep-dive-lineage-collection.md) - the Confidence Curator agent described there is one of the standing jobs this layer generalizes.
* [L11 Query APIs, Review UI, and PR-Gate Experience PRD](/references/apis-review-ui-and-pr-gate.md) - the implemented review and PR-gate surface where agent recommendations would land.

## Citations

1. [Agentic Experiences.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Agentic Experiences.dc.html)
