from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).parents[1]
TARGET_ARCHITECTURE = "docs/architecture/lineage-platform-target.md"
TARGET_ARCHITECTURE_HTML = "docs/architecture/lineage-platform-target.html"
LINEAGE_DEPLOYMENT_EXPLORER = "docs/architecture/lineage-deployment-explorer.html"


def _read(relative_path: str) -> str:
    path = ROOT / relative_path
    assert path.exists(), f"Missing required documentation: {relative_path}"
    return path.read_text(encoding="utf-8")


def _renderer() -> ModuleType:
    name = "render_target_architecture"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = ROOT / "scripts" / f"{name}.py"
    assert path.exists(), "Missing target architecture renderer"
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    # ``slots=True`` dataclasses resolve their own module through ``sys.modules``.
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def _architecture():
    return _renderer().parse_architecture(_read(TARGET_ARCHITECTURE))


def test_readme_documents_every_local_operator_command_and_url() -> None:
    readme = _read("README.md")

    for command in ("make setup", "make dev", "make reset", "make test", "make build"):
        assert command in readme
    assert "http://127.0.0.1:5173" in readme
    assert "http://127.0.0.1:8000/healthz" in readme
    assert "## Demo walkthrough" in readme


def test_operator_handoff_documents_workflows_generated_safety_and_acceptance() -> None:
    readme = _read("README.md")

    for command in (
        "worker --drain --max-messages 100",
        "make workflow-check",
        "make acceptance-smoke",
        "make aws-deploy",
        "make aws-smoke",
        "make aws-cleanup",
    ):
        assert command in readme
    for heading in (
        "## Workflow trigger table",
        "## Local-to-AWS mapping",
        "## Generated workflow safety",
        "## Fault injection and acceptance evidence",
        "## AWS ephemeral verification",
    ):
        assert heading in readme
    for link in (
        "docs/plans/2026-08-05-lineage-collection-architecture-refactor-design.md",
        "docs/plans/2026-08-05-lineage-collection-architecture-refactor.md",
        "docs/acceptance/lineage-platform-acceptance.md",
        "docs/build-prds/README.md",
    ):
        assert link in readme


def test_lineage_deployment_explorer_covers_collection_deployment_and_product_flows() -> None:
    explorer = _read(LINEAGE_DEPLOYMENT_EXPLORER)
    lowered = explorer.lower()

    for journey in ("sca collection", "runtime corroboration", "api and ui", "deployment"):
        assert journey in lowered
    for service in (
        "ecs fargate",
        "lambda",
        "sns",
        "sqs",
        "kinesis",
        "eventbridge",
        "api gateway",
        "cloudfront",
        "dynamodb",
        "s3",
        "ecr",
        "neptune",
    ):
        assert service in lowered
    for target in (
        "intake",
        "control-stage",
        "classification",
        "coverage",
        "runtime-validation",
        "consolidation",
        "proposal",
        "publication",
        "deployment",
        "product-api",
    ):
        assert f'data-component="{target}"' in explorer
    for status in (
        "verified",
        "synthesized",
        "partial",
        "planned",
        "AWS_REQUIRED",
        "NOT_CONFIGURED",
    ):
        assert status in explorer
    for hook in (
        'aria-live="polite"',
        'role="dialog"',
        'aria-modal="true"',
        "prefers-reduced-motion",
    ):
        assert hook in explorer
    for source in (
        "infra/assets/lambda/Dockerfile",
        "infra/assets/sca/Dockerfile",
        "infra/lib/runtime-assets.ts",
        "infra/lib/engines-stack.ts",
        "infra/lib/intake-stack.ts",
        "scripts/deploy_ephemeral_aws.sh",
        "apps/api/src/lineage_api/application/workflows/definitions.py",
        "docs/architecture/lineage-platform-target.md",
    ):
        assert source in explorer


def test_coverage_matrix_accounts_for_every_component() -> None:
    coverage = _read("docs/prototype-coverage.md")

    for number in range(1, 17):
        assert f"L{number:02d}" in coverage
    for state in ("Implemented", "Fixture adapter", "Interface only", "Deferred"):
        assert state in coverage
    assert "Out of prototype scope" in coverage
    for state in ("LOCAL_PASS", "SYNTH_PASS", "AWS_REQUIRED", "NOT_CONFIGURED"):
        assert state in coverage
    assert "Tasks 17–20" in coverage


def test_ambiguity_register_has_required_fields_ctx_items_and_inconsistencies() -> None:
    ambiguities = _read("docs/prd-ambiguities.md")

    for heading in (
        "ID",
        "Classification",
        "Source",
        "Impact",
        "Prototype decision",
        "Production owner",
    ):
        assert heading in ambiguities
    for number in range(1, 18):
        assert f"CTX-{number:02d}" in ambiguities
    for gap in ("G-L01-1", "G-L03-3", "G-L04-1", "G-L05-1", "G-L06-1", "G-L09-2"):
        assert gap in ambiguities
    assert "## Cross-document inconsistencies" in ambiguities
    for inconsistency in ("INC-01", "INC-02", "INC-03"):
        assert inconsistency in ambiguities

    table_rows = [line for line in ambiguities.splitlines() if re.match(r"\| (CTX|G-|INC-|PA-)", line)]
    assert len(table_rows) >= 26
    assert all(row.count("|") >= 7 for row in table_rows)


def test_acceptance_spec_covers_every_build_unit_cadence_and_historical_reference() -> None:
    acceptance = _read("docs/acceptance/lineage-platform-acceptance.md")
    readiness = _read("docs/component-prds/99-implementation-readiness-review.md")

    for number in range(1, 17):
        assert f"B{number:02d}" in acceptance
    for cadence in (
        "Every PR",
        "Every deployment",
        "Nightly",
        "Weekly",
        "Monthly",
        "Quarterly",
    ):
        assert cadence in acceptance
    for historical_section in range(2, 8):
        assert f"Test Suite §{historical_section}" in acceptance

    assert "AcceptanceEvidenceManifest" in acceptance
    assert "../acceptance/lineage-platform-acceptance.md" in readiness


def test_build_prds_are_complete_traceable_and_linked_from_delivery_plan() -> None:
    build_units = (
        "contracts-and-correlation",
        "catalog-and-resolver",
        "evidence-and-control-stores",
        "event-intake-and-lanes",
        "classifier-and-orchestrator",
        "sca-worker-and-rule-packs",
        "llm-inference-gateway",
        "runtime-session-and-ingestion",
        "runtime-emitters-and-integrations",
        "consolidation-and-confidence",
        "proposal-review-and-policy",
        "publisher-and-projection-manager",
        "query-impact-and-pr-gate",
        "review-and-operations-ui",
        "operations-telemetry-and-recovery",
        "platform-iac-and-delivery",
    )
    required_headings = (
        "Ownership",
        "Boundary",
        "Contracts",
        "State and failure model",
        "Data ownership",
        "Infrastructure bill of materials",
        "Local adapter",
        "Security and privacy",
        "SLOs",
        "Observability",
        "Acceptance criteria",
        "Deployment and rollback",
        "Dependencies",
        "Definition of Ready",
        "Definition of Done",
    )
    index = _read("docs/build-prds/README.md")
    delivery = _read("docs/component-prds/16-delivery-plan-and-dependencies.md")

    for number, slug in enumerate(build_units, start=1):
        build_id = f"B{number:02d}"
        relative = f"docs/build-prds/{build_id}-{slug}.md"
        prd = _read(relative)
        assert f"# {build_id}" in prd
        for heading in required_headings:
            assert f"## {heading}" in prd, f"{build_id} missing {heading}"
        assert re.search(rf"\| {build_id}-AC-\d{{3}} \|", prd)
        assert re.search(r"\.\./component-prds/\d{2}-[^)]+\.md", prd)
        assert f"{build_id}-{slug}.md" in index

    assert "Build order" in index
    assert "../acceptance/lineage-platform-acceptance.md" in index
    assert "../build-prds/README.md" in delivery


def test_normative_design_contains_all_six_diagram_views_and_honest_limits() -> None:
    design = _read("docs/plans/2026-08-05-lineage-collection-architecture-refactor-design.md")

    assert design.count("```mermaid") >= 6
    for view in (
        "Trigger and orchestration view",
        "Runtime evidence view",
        "Correctness and recovery view",
        "Multi-AZ and regional recovery view",
        "Local-to-production adapter view",
    ):
        assert view in design
    assert "AWS_REQUIRED" in design
    assert "five-minute stage-to-swap" in design
    assert "No production recovery drill has run" in design


REQUIRED_TARGET_LAYERS = (
    ("sources", "Triggers and evidence sources"),
    ("ingress", "Edge and durable intake"),
    ("control", "Durable control plane"),
    ("orchestration", "Versioned orchestration"),
    ("engines", "Collection and evidence engines"),
    ("trust", "Evidence, consolidation, and trust"),
    ("projections", "Projections and product surfaces"),
    ("platform", "Security, operations, and recovery"),
)

REQUIRED_TARGET_STATUSES = ("verified", "synthesized", "partial", "planned")

DURABLE_TRANSPORT_NODES = frozenset(
    {"bus", "archive", "qInt", "qEvt", "qBulk", "dlq", "rtStream"}
)

IMMUTABLE_OBJECT_STORE_NODES = frozenset({"s3Evidence", "s3Package"})


def _target_component_text() -> str:
    nodes = _architecture().nodes
    return "\n".join(f"{node.title} {node.detail}" for node in nodes).lower()


def test_target_architecture_declares_required_layers_and_status_legend() -> None:
    architecture = _architecture()

    assert [layer.identifier for layer in architecture.layers] == [
        identifier for identifier, _ in REQUIRED_TARGET_LAYERS
    ]
    for index, (identifier, title) in enumerate(REQUIRED_TARGET_LAYERS, start=1):
        layer = architecture.layers[index - 1]
        assert layer.identifier == identifier
        assert layer.title == f"{index} - {title}"
        assert layer.nodes, f"layer {identifier} declares no components"

    assert tuple(status.name for status in architecture.statuses) == REQUIRED_TARGET_STATUSES
    used = {node.status for node in architecture.nodes}
    assert used == set(REQUIRED_TARGET_STATUSES)

    document = _read(TARGET_ARCHITECTURE)
    assert "### Status legend" in document
    for status in REQUIRED_TARGET_STATUSES:
        assert f"`{status}`" in document
    for evidence in ("LOCAL_PASS", "LOCAL_REAL_REPOSITORY_PASS", "SYNTH_PASS", "NOT_CONFIGURED"):
        assert evidence in document
    assert "AWS_REQUIRED" in document


def test_target_architecture_covers_every_required_production_component() -> None:
    components = _target_component_text()

    for required in (
        "github",
        "jenkins",
        "tas",
        "eventbridge schedules",
        "deployment signals",
        "runtime observation",
        "api gateway",
        "lambda",
        "eventbridge lineage bus",
        "interactive fifo lane",
        "events fifo lane",
        "bulk lane",
        "dead-letter",
        "kinesis",
        "dynamodb",
        "durable commands",
        "dedupe",
        "leases",
        "stage ledger",
        "coverage",
        "outbox",
        "fencing tokens",
        "baseline workflow",
        "incremental workflow",
        "prgate workflow",
        "nightlyreconciliation workflow",
        "deployment promotion workflow",
        "runtime session orchestration",
        "repository acquisition",
        "classifier",
        "sca workers",
        "spring data jpa",
        "sql and postgresql",
        "python analyzer",
        "otel",
        "openlineage",
        "spark and dask",
        "residue",
        "s3 immutable evidence",
        "consolidation",
        "confidence",
        "proposal",
        "review",
        "fenced publisher",
        "neptune",
        "opensearch",
        "product and query api",
        "pr gate",
        "cloudfront",
        "oidc",
        "iam",
        "kms",
        "appconfig",
        "cloudwatch",
        "x-ray",
        "cloudtrail",
        "redrive",
        "replay",
        "backup",
        "recovery",
    ):
        assert required in components, f"target architecture omits {required}"


def test_target_architecture_shows_no_local_mapping_as_a_component() -> None:
    components = _target_component_text()

    for forbidden in (
        "sqlite",
        "vite",
        "in-process",
        "local file",
        "filesystem",
        "localhost",
        "127.0.0.1",
    ):
        assert forbidden not in components, f"local mapping leaked into the target: {forbidden}"


def test_target_architecture_gives_every_arrow_a_meaning_and_honest_asynchrony() -> None:
    architecture = _architecture()

    for edge in architecture.edges:
        assert edge.label.strip(), f"unlabelled arrow {edge.source} -> {edge.target}"

    for edge in architecture.edges:
        touches_transport = (
            edge.source in DURABLE_TRANSPORT_NODES or edge.target in DURABLE_TRANSPORT_NODES
        )
        if touches_transport:
            assert edge.durable, (
                f"{edge.source} -> {edge.target} crosses a durable transport "
                "but is drawn as a synchronous dependency"
            )
        if edge.target in IMMUTABLE_OBJECT_STORE_NODES:
            assert edge.durable, (
                f"{edge.source} -> {edge.target} writes immutable evidence "
                "but is drawn as a synchronous dependency"
            )

    assert any(edge.durable for edge in architecture.edges)
    assert any(not edge.durable for edge in architecture.edges)


def test_target_architecture_makes_trust_invariants_visually_explicit() -> None:
    architecture = _architecture()
    by_id = {node.identifier: node for node in architecture.nodes}
    edges = {(edge.source, edge.target): edge for edge in architecture.edges}

    assert by_id["review"].shape == "gate", "human review must be drawn as a gate"
    assert ("review", "publish") in edges
    assert [source for source, target in edges if target == "publish"] == ["review"]

    assert edges[("publish", "ctrlPtr")].label == "fencing token compare-and-set"
    assert edges[("sfnDeployment", "ctrlPtr")].label == "exact artifact digest match"
    assert edges[("coverageGate", "consolidate")].label == "completeness verdict"
    assert "non-blocking" in edges[("rtValidate", "consolidate")].label


def test_target_architecture_documents_evidence_boundaries_and_gaps() -> None:
    document = _read(TARGET_ARCHITECTURE)

    assert "## 4. Evidence boundaries" in document
    for boundary in (
        "Source content boundary",
        "Runtime metadata boundary",
        "Non-blocking runtime boundary",
        "Human trust boundary",
        "Fencing boundary",
        "Production runtime boundary",
        "Error-surface boundary",
    ):
        assert boundary in document, f"missing evidence boundary: {boundary}"

    assert "## 3. Layer responsibilities" in document
    assert "## 5. Reconciliation with normative plans" in document
    assert "## 6. Known target gaps" in document
    for plan in (
        "2026-08-05-lineage-collection-architecture-refactor-design.md",
        "2026-08-08-production-aws-application-completion-design.md",
        "2026-08-07-runtime-lineage-instrumentation-design.md",
        "2026-08-09-real-java-spring-repository-lineage-design.md",
        "2026-08-10-repository-collection-ui-api-design.md",
        "../acceptance/lineage-platform-acceptance.md",
        "../prototype-coverage.md",
    ):
        assert plan in document, f"target architecture does not reconcile {plan}"


def test_target_architecture_html_is_offline_and_matches_the_mermaid_source() -> None:
    renderer = _renderer()
    committed = _read(TARGET_ARCHITECTURE_HTML)

    assert committed == renderer.build(), (
        "docs/architecture/lineage-platform-target.html is stale; "
        "run 'python scripts/render_target_architecture.py'"
    )

    architecture = _architecture()
    for node in architecture.nodes:
        assert f'data-node="{node.identifier}"' in committed
        assert f'data-status="{node.status}"' in committed
        assert node.title in committed
    for layer in architecture.layers:
        assert f'data-layer="{layer.identifier}"' in committed
    for edge in architecture.edges:
        assert f'data-edge="{edge.source}-&gt;{edge.target}"' in committed
    for status in REQUIRED_TARGET_STATUSES:
        assert f'data-legend="{status}"' in committed

    assert not re.search(r"https?://", committed), "the offline rendering must not fetch resources"
    assert "<script" not in committed
    assert "AWS_REQUIRED" in committed


ARCHITECTURE_DECK = ROOT / "docs" / "architecture-deck"

# Services the decks may name only if they also carry a PLANNED marker, because they do not
# appear anywhere in infra/lib. Kept as a literal list so a reviewer sees exactly what is claimed.
UNBUILT_AWS_SERVICES = (
    "Bedrock",
    "Firehose",
    "OpenSearch",
    "CloudTrail",
)


def _deck_paths() -> list[Path]:
    decks = sorted(ARCHITECTURE_DECK.glob("*.dc.html"))
    assert decks, "the architecture deck is missing"
    return decks


def _deck_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_architecture_deck_defers_to_the_normative_target_view() -> None:
    for deck in _deck_paths():
        text = _deck_text(deck)
        assert "docs/architecture/lineage-platform-target.md" in text, (
            f"{deck.name} does not point at the normative production AWS view"
        )
        assert "component PRDs (L01–L16)" in text, (
            f"{deck.name} does not state that it presents the component PRDs"
        )


def test_architecture_deck_never_claims_live_aws_evidence() -> None:
    # The roadmap is a forward-looking plan; its own strip already frames figures as targets.
    for deck in _deck_paths():
        text = _deck_text(deck)
        if deck.name == "Delivery Roadmap.dc.html":
            assert "figures are targets" in text
            continue
        assert "AWS_REQUIRED" in text, f"{deck.name} omits the AWS_REQUIRED evidence banner"


def test_architecture_deck_marks_every_service_absent_from_infrastructure() -> None:
    infrastructure = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((ROOT / "infra" / "lib").glob("*.ts"))
    ).lower()

    for service in UNBUILT_AWS_SERVICES:
        assert service.lower() not in infrastructure, (
            f"{service} now exists in infra/lib; promote it out of UNBUILT_AWS_SERVICES "
            "and update the deck status"
        )

    for deck in _deck_paths():
        # The roadmap is a plan for work not yet done, so naming unbuilt services is correct
        # there; its provenance strip already frames every figure as a target.
        if deck.name == "Delivery Roadmap.dc.html":
            continue
        text = _deck_text(deck)
        for service in UNBUILT_AWS_SERVICES:
            for line in text.splitlines():
                if service not in line:
                    continue
                assert "PLANNED" in line, (
                    f"{deck.name} names {service}, which is absent from infra/lib, "
                    "without a PLANNED marker"
                )


def test_architecture_deck_analyzer_packs_match_the_closed_registry() -> None:
    registry = _read("apps/api/src/lineage_api/services/analyzer_registry.py")
    packs = set(re.findall(r'AnalyzerDefinition\(\s*"([a-z0-9-]+-v\d+)"', registry))
    assert packs == {"python-fixture-v1", "java-spring-data-jpa-v1"}, packs

    for deck in _deck_paths():
        text = _deck_text(deck)
        if "Rule packs" not in text:
            continue
        for line in text.splitlines():
            if "Rule packs" not in line:
                continue
            assert "Spring Data JPA" in line, f"{deck.name} omits the built Java/Spring pack"
            for unsupported in ("Kotlin", "JS/TS", "Go"):
                if unsupported in line:
                    assert "PLANNED" in line, (
                        f"{deck.name} claims a {unsupported} rule pack that is not registered"
                    )


def test_architecture_deck_renders_without_any_network_access() -> None:
    for deck in _deck_paths():
        text = _deck_text(deck)
        assert not re.search(r"https?://", text), (
            f"{deck.name} fetches an external resource and will not render offline"
        )
    fonts = ARCHITECTURE_DECK / "fonts.css"
    assert fonts.exists(), "the vendored font stylesheet is missing"
    stylesheet = fonts.read_text(encoding="utf-8")
    assert stylesheet.count("@font-face") == 7
    assert not re.search(r"src:\s*url\(https?://", stylesheet)
