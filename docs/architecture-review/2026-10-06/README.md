# Lineage collection architecture review

This package assesses the implementation at commit [`32958a35cdf49341b15a4d6cb05f596811396b06`](https://github.com/Kart-rc/lineage-collection/tree/32958a35cdf49341b15a4d6cb05f596811396b06) and proposes an enterprise engineering plan. Implemented behavior, observed gaps, and proposed requirements are distinguished throughout. The proposed architecture is not a claim of production readiness.

## Start here

- [Interactive guide](lineage-interactive-guide.html): download the HTML file and open it in a modern browser. It is self-contained and requires no server, account, or network connection. GitHub shows its source rather than running the interactive controls.
- Architecture review, market benchmark, PRD, TDD, and ATDD coverage: [Markdown](architecture-review.md), [Word](Lineage_Collection_Architecture_Review.docx), or [PDF](Lineage_Collection_Architecture_Review.pdf).
- Technical discussion guide: [Markdown](technical-discussion-guide.md), [Word](Lineage_Collection_Technical_Discussion_Guide.docx), or [PDF](Lineage_Collection_Technical_Discussion_Guide.pdf).
- Architecture diagrams: current [PNG](assets/current_architecture.png) / [SVG](assets/current_architecture.svg); proposed target [PNG](assets/target_architecture.png) / [SVG](assets/target_architecture.svg).

## Design direction

The proposed first milestone connects Java/Spring services through Kafka and Spark/SQL to an output table and downstream service. Use OpenTelemetry first for services and feasible field-evidence customization, a separate SDK only where that is infeasible, Spark-native capture, and semantic normalization into OpenLineage. Independently authored acceptance tests measure runtime coverage and field-lineage correctness separately.

## Validation

The Word and PDF documents were rendered and visually reviewed. The interactive guide passed 689 DOM/JavaScript assertions; real-browser visual, responsive, keyboard, and print verification remains outstanding. Its calculators and ATDD counts are educational examples, not product measurements. See the [interactive QA report](lineage-interactive-guide-qa.md), [recorded results](lineage-guide-qa-results.json), and [reproducible test script](lineage-interactive-guide.qa.cjs).

This is a documentation-only package. The underlying application was not changed or deployed.
