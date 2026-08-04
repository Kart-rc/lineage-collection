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


def test_coverage_matrix_accounts_for_every_component() -> None:
    coverage = _read("docs/prototype-coverage.md")

    for number in range(1, 17):
        assert f"L{number:02d}" in coverage
    for state in ("Implemented", "Fixture adapter", "Interface only", "Deferred"):
        assert state in coverage
    assert "Out of prototype scope" in coverage


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
