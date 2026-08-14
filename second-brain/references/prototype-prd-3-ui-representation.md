---
type: Reference
title: "Throughline Prototype: PRD 3 UI Representation"
description: The prototype PRD specifying altitude-based navigation and four coordinated lenses so a 2,000-node graph is never rendered whole.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/PRD 3 - UI Representation.dc.html
tags: [prototype, throughline, ui, navigation, lenses]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: PRD 3 UI Representation

Third Throughline PRD (v0.9 draft, owner Design + Data Platform). Governing principle: you cannot
draw a 2,000-node graph and expect anyone to read it, so never render the whole graph — navigate by
altitude. Start at domains, drill to the team-aligned business application, then services, datasets,
and columns, loading only what is in view. A working prototype already demonstrated the model.

Key specifications:

- Altitude navigation: progressive disclosure down the containment hierarchy from PRD 1
  (Org > Domain > App > Service > dataset > column), with each level defining what renders
  immediately and what loads on demand.
- Four coordinated lenses over one graph — Lineage ("where does data flow"), Interactions ("who
  calls whom"), Impact ("what breaks"), and Provenance ("how do we know") — sharing one altitude
  position and selection so switching a lens never loses the user's place.
- Layout: a persistent three-region shell — top bar (search, lens switch, scope, profile),
  altitude/breadcrumb bar (Org > Domain > App > Service, Domains/Apps/Services toggle, stat
  strip), then canvas plus inspector. Lenses swap canvas and inspector content; the chrome stays
  put.
- Visual encoding is consistent across lenses so a color or line style always means the same
  thing: channel is encoded by line style, state by node treatment, and confidence follows PRD 1's
  bands — Verified edges solid, Probable solid-thin, Inferred dashed and lower-opacity — so trust
  is readable on the canvas itself.
- Standard PRD scaffolding follows: design principles, functional requirements with priorities,
  success criteria, rollout phasing, and risks/open questions, plus a link out to the live
  prototype.

## Related

* [Throughline Prototype: PRD 1 Lineage Collection](/references/prototype-prd-1-lineage-collection.md) - defines the containment hierarchy and confidence bands this UI encodes
* [Throughline Prototype: PRD 2 Impact Analysis](/references/prototype-prd-2-impact-analysis.md) - the engine behind the Impact lens specified here
* [Throughline Prototype: Final Product Document](/references/prototype-throughline-final.md) - the interactive prototype embodying this PRD's lenses and altitudes
* [B14 Review and Operations UI](/references/b14-review-and-operations-ui.md) - the implemented UI build PRD descended from this representation model

## Citations

1. [PRD 3 - UI Representation.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/PRD 3 - UI Representation.dc.html)
