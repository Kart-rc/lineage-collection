from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _read(relative_path: str) -> str:
    path = ROOT / relative_path
    assert path.exists(), f"Missing required documentation: {relative_path}"
    return path.read_text(encoding="utf-8")


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
