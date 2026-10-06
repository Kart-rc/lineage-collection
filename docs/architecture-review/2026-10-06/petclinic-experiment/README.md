# OTel-first service lineage: real Spring Petclinic experiment

Executed 6 October 2026, UTC. Result: the service slice works, with an important limit. The Java auto-agent observed HTTP and JDBC activity. A small OTel interceptor added executed field-mapping declarations. Independent output assertions caught a real application mutation that the unchanged declarations did not detect.

## What actually ran

Official repository: https://github.com/spring-projects/spring-petclinic

Pinned source: [500158f732419217507c7656904b8e6aa1bcc0d6](https://github.com/spring-projects/spring-petclinic/tree/500158f732419217507c7656904b8e6aa1bcc0d6), committed 29 September 2026. Upstream license: Apache-2.0; included in [LICENSE-upstream.txt](LICENSE-upstream.txt). Bundled OpenLineage schema licensing and component attribution are retained in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Source archive SHA-256: 4932ae0209375b3cca3dd7c8f6f9345498d75dedc5970dcfa91814f1b830768d.

The real Spring Boot 4.1.0 application ran against a fresh, in-memory H2 database on a loopback-only HTTP port. No MockMvc or mocked repository was used for the lineage experiment. The public GET /owners/1 and /owners/2 routes rendered the checked-in sample owners. GET /owners/999999 exercised the application's existing HTTP 500 failure behavior. Each mode launched a separate JVM and database, then shut it down after telemetry flush.

Tools: Eclipse Temurin JDK 21.0.12.1+1, Maven 3.9.16, OTel Java agent 2.32.0, OTel API dependency 1.59.0, JaCoCo 0.8.15, Python 3.12. Dependencies and source came from official release sites/Maven Central. The JDK, Maven distribution and OTel agent were checked against their official release checksums. See requirements.txt for Python package pins.

The same application JAR was used in all three modes. Baseline disabled the custom interceptor. Enhanced enabled it. Mutant enabled it but overrode the owner-details template to display only first name instead of first name + space + last name. Artifact and active-template hashes are recorded per request. The source-hash-to-JAR-template equality check for the recorded baseline/enhanced runs is retained separately with its verification timestamp.

## Observed results

| Mode | Actual HTTP behavior | OTel evidence per successful request | Field result |
|---|---|---|---|
| Auto-agent baseline | Both owners render correctly | 1 HTTP SERVER span, 4 JDBC SELECT spans, 2 of those select owners | No field-mapping declaration; no accepted field edges |
| OTel customized | Both owners render correctly | Same HTTP/JDBC spans plus 1 metadata-only event on the server span | All 5 declared value edges and 4 normalized filter influences match independent gold |
| Real template mutant | First name only; both independent name assertions fail | Same boundary spans and unchanged mapping declaration | Declaration comparison still passes; combined acceptance rejects the mutation |

Each mode captured 100 spans including startup; 27 belong to the three explicitly registered request traces (11 + 11 + 5). Those are captured-file counts, not a claim about how many spans should exist. The missing-owner request had one HTTP SERVER span, one JDBC SELECT, zero mapping events and a FAIL OpenLineage terminal event.

The critical finding is the mutant. A decorator/interceptor can prove that a declared mapping accompanied execution; it cannot prove that the declaration describes the deployed transformation. The OpenLineage custom facet therefore retains DECLARED_AND_EXECUTED and businessOutputValidated=false. Independent validation is recorded in comparison.json rather than silently promoting sensor evidence to observed field causality.

## The actual field slice

Target: four rendered cells in the first owner-summary HTML table. Pets, visits, links and other response content are excluded.

- PUBLIC.OWNERS.first_name and last_name → logical owner-summary.name: concatenate with a space, then HTML text rendering
- PUBLIC.OWNERS.address → owner-summary.address: HTML text rendering
- PUBLIC.OWNERS.city → owner-summary.city: HTML text rendering
- PUBLIC.OWNERS.telephone → owner-summary.telephone: HTML text rendering
- PUBLIC.OWNERS.id: row-selection FILTER influence on the four targets, never an extra value source

The physical source namespace is jdbc:h2:mem:petclinic; the logical output namespace is logical://spring-petclinic. HTML rendering is a transformation at this boundary, even where decoded DOM text equals the database string. No edge is called an identity copy.

## Evidence and OpenLineage

The Java agent exports actual OTLP JSON using its logging-otlp exporter. The interceptor uses only the OTel API and the agent-owned SDK; there is no separate lineage SDK or exporter. Its custom event contains a versioned contract, SHA-256 digest, operation and evidence class. It does not inspect model values or method arguments.

The semantic adapter checks the scheduled trace context, HTTP route/method/path/status, service/build identity, parent chains, H2 database identity, JDBC errors, dropped span-content counters and contract digest. It then emits a stable service-operation Job and one Run per real HTTP attempt, with START plus COMPLETE or FAIL. It preserves trace/span references and both JAR and active-template identity. Missing or inconsistent evidence produces no accepted field edges.

All 18 emitted lifecycle events across 9 HTTP attempts validate locally against bundled OpenLineage 2-0-2, ColumnLineageDatasetFacet 1-2-0, SchemaDatasetFacet 1-2-0 and the bundled custom evidence schema. The custom schema uses an immutable, content-addressed URN; it is distributed with this prototype, not hosted in a public schema registry. Evidence retains declared, observed-failure and incomplete states. OpenLineage backend ingestion, facet retention and UI interpretation were not tested.

Example enhanced run:

- Trace: d90bd324164046dfb56066d4a7ef382b
- OpenLineage run UUID: 8328a748-d29d-57a6-9add-f394617e0612
- Raw captured spans, sanitized for delivery: evidence/enhanced/spans.json
- Events: evidence/enhanced/owner_1_seed-openlineage.json
- Actual response: evidence/enhanced/owner_1_seed.html
- Independent comparison: evidence/comparison.json

## Independent gold and negative controls

Gold was authored from the pinned source, schema, templates and seed data before its author inspected the interceptor, adapter or telemetry. Ten source files were verified byte-for-byte against pinned GitHub blobs. Gold SHA-256: 79ac0307d93704bf81669a908f067e8c2c618fbd6d9f78cdef0efc6059397334. The verifier checks the frozen checksums before scoring.

Real application mutation: first-name-only template, exercised for both owners and rejected by field-specific DOM assertions. This is a real rerun of the service, not an edited response artifact.

Nine additional controls all detected:

1. Remove the mapping event from captured evidence
2. Remove all JDBC spans from captured evidence
3. Remove the HTTP SERVER span from captured evidence
4. Omit the last-name edge
5. Invent ID as a value source
6. Relabel rendering as IDENTITY
7. Change output dataset identity
8. Add an untyped extra source
9. Change input database identity

The first three are offline capture-loss simulations against real recorded traces; they are not live collector outage tests. The remaining six mutate the adapted event. An independent code/evidence reviewer also checked malformed identity, JDBC error, orphan-parent and dropped-content cases. The final harness has strict scenario-presence and exit-code gates; missing files, unexpected positive failures or escaped negative controls fail verification.

## Coverage, with denominators

| Measure | Result | Denominator and scope |
|---|---|---|
| Scenario execution | 9/9 HTTP attempts; all expected experiment verdicts satisfied | 3 scenarios × baseline/enhanced/mutant; includes intentional mutant failures |
| Main enhanced acceptance | 3/3 | Two successful owner reads plus existing missing-owner failure semantics |
| Potential operation exercise | 1/7 | Seven GET/POST route definitions in OwnerController only; other controllers are outside this inventory |
| Java branch coverage | 1/20 (5%) in OwnerController | Enhanced real-HTTP run; JaCoCo does not count exception handlers as branches |
| All application branch coverage | 14/142 (9.9%) | 24 application classes including the experimental interceptor; enhanced run only |
| Typed value edges | Precision 5/5, recall 5/5 per successful enhanced scenario | Five independently adjudicated source-target-transform edges in this slice |
| Filter influence | Precision 4/4, recall 4/4 per successful enhanced scenario | One dataset-level FILTER source normalized to the four scoped output targets |
| Baseline value recall | 0/5; precision N/A | No field edges emitted; never treat an empty prediction set as perfect precision |
| Boundary association | 4/4 required associations across two enhanced successes | HTTP→JDBC ancestor linkage and successful mapping-event→HTTP association per scenario; this does not prove field causality |
| Required sensor presence | HTTP 3/3, owners SELECT 3/3, success mapping 2/2 | Enhanced run registry; zero successful mappings expected for the failed request |
| End-to-end capture completeness | NOT ESTABLISHED | No durable collector acknowledgements, emitter ledger or proof every possible span arrived |
| Negative controls | 9/9 detected | Explicit list above; plus the actual template mutation detected in both positive fixtures |

JaCoCo XML and per-class counters are included. Two focused upstream suites also passed: ClinicServiceTests 12/12 and OwnerControllerTests 18/18. The first test attempt hit Mockito's JDK self-attach limitation; rerunning with Mockito's official startup Java agent produced 30/30 pass. These mock-based upstream tests are separate regression evidence and are not the lineage proof. The full upstream suite, Docker/MySQL/PostgreSQL profiles, formatting and Checkstyle were not run.

## Reproduce

Requires Linux x86-64, Python 3.12+, outbound access to the linked official downloads/Maven Central and permission to listen on localhost. No Docker, account, paid API, deployment or remote write is needed.

1. Create a virtual environment and install requirements.txt.
2. Run python3 harness/bootstrap.py. This downloads the pinned official source, JDK, Maven and agents into this directory; dependencies are not bundled.
3. Run python3 harness/instrument.py to add the isolated interceptor and OTel API dependency.
4. Run bash harness/build.sh. The optional Git build-metadata plugin is skipped because source comes from a pinned archive. Formatting/Checkstyle and upstream tests are explicitly skipped in this build step.
5. Run bash harness/run_all.sh. It launches three disposable JVMs, performs real HTTP calls, terminates each, validates events and comparisons, and generates coverage XML.
6. To recheck the delivered evidence without building Java, install requirements.txt and run python3 harness/verify.py. Expected final line: FINAL ACCEPTANCE GATE: PASS.

Existing evidence directories are archived before a fresh run to prevent stale results being reused. Public fixture values are retained only in the explicit HTTP result/gold artifacts; telemetry and OpenLineage contain no checked fixture names/address/phone values. Delivered telemetry removes host and process resource attributes and normalizes execution-root paths. The publication copy also replaces the hostname-derived JaCoCo session label with a neutral experiment identifier; SANITIZATION.json records original/delivered hashes. Request paths and synthetic owner IDs remain as evidence. There are no credentials in the package.

## Limits and next decision

This demonstrates one read path in a real service, not the entire application or the full Java→Kafka→Spark→table→consumer chain. Petclinic has no Kafka/Spark workload here. No Spark-native/OpenLineage listener, broker acknowledgement, async propagation, sink commit, production overhead or scaling claim is made. The separate existing lineage-collection product/backend was not integrated or validated by this standalone prototype.

The next extension should be a small explicit producer→Kafka→Spark SQL→committed table acceptance fixture, retaining independent gold and result assertions, using Spark-native OpenLineage for engine lineage. First adopt deployment/contract freshness checks and durable capture accounting; the mutant shows why both matter. A separate SDK was unnecessary for this service slice.

References: [OTel Java agent](https://opentelemetry.io/docs/zero-code/java/agent/), [Java exporter configuration](https://opentelemetry.io/docs/languages/java/configuration/), [OpenLineage column lineage](https://openlineage.io/docs/spec/facets/dataset-facets/column_lineage_facet/), [OTel release 2.32.0](https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/tag/v2.32.0).
