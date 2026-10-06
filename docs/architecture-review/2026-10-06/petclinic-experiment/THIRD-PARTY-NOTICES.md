# Third-party materials in this experiment

## Spring Petclinic

The synthetic fixture data, rendered sample responses, template/source-derived gold, and source references originate from official Spring Petclinic at commit [`500158f732419217507c7656904b8e6aa1bcc0d6`](https://github.com/spring-projects/spring-petclinic/tree/500158f732419217507c7656904b8e6aa1bcc0d6). The experiment uses those materials for a bounded owner-summary validation and records its template mutation explicitly. The application source and executable binaries are not bundled.

Spring Petclinic is licensed under Apache-2.0. Its retained license is [LICENSE-upstream.txt](LICENSE-upstream.txt). Upstream source attribution, fixture provenance, and byte hashes are recorded in [gold/pinned_source_manifest.json](gold/pinned_source_manifest.json). Experiment modifications are described in the [README](README.md), the reproduction scripts, and the mutation evidence.

## OpenLineage schemas

The following bundled schemas come from the OpenLineage project and are licensed under Apache-2.0:

- `schemas/OpenLineage.json`: [OpenLineage schema 2-0-2](https://openlineage.io/spec/2-0-2/OpenLineage.json)
- `schemas/ColumnLineageDatasetFacet.json`: [ColumnLineageDatasetFacet 1-2-0](https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json)
- `schemas/SchemaDatasetFacet.json`: [SchemaDatasetFacet 1-2-0](https://openlineage.io/spec/facets/1-2-0/SchemaDatasetFacet.json)

Retained copies: [LICENSE-OpenLineage.txt](LICENSE-OpenLineage.txt) and [NOTICE-OpenLineage.txt](NOTICE-OpenLineage.txt). These notices were retrieved from the official OpenLineage repository at commit [`3771fbb904eb6dce9a11fe16f431d7737cdba0d9`](https://github.com/OpenLineage/OpenLineage/tree/3771fbb904eb6dce9a11fe16f431d7737cdba0d9): [license source](https://github.com/OpenLineage/OpenLineage/blob/3771fbb904eb6dce9a11fe16f431d7737cdba0d9/LICENSE), [notice source](https://github.com/OpenLineage/OpenLineage/blob/3771fbb904eb6dce9a11fe16f431d7737cdba0d9/NOTICE.txt). This commit pins the retained notices; it is not a claim about the original schema release commit. The upstream notice is reproduced in full; references to other OpenLineage components do not imply those components are bundled here.

## Experiment-authored material and runtime dependencies

The harness, mapping contract, custom evidence schema, and independent comparison logic were authored for this experiment. The third-party license files above apply to the identified upstream materials. This publication does not assign a new license to the experiment-authored material or change this repository's licensing.

JDK, Maven, OpenTelemetry agent/API, JaCoCo, Python dependencies, and the Petclinic executable are not bundled. Reproduction downloads or installs those components separately; their own licenses and notices apply. The publication copy removes host/process telemetry attributes, normalizes execution-root paths, and neutralizes the JaCoCo session identifier. See [SANITIZATION.json](SANITIZATION.json) for transformation provenance and [SHA256SUMS](SHA256SUMS) for delivered byte hashes.
