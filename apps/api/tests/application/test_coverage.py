from __future__ import annotations

import pytest

from lineage_api.application.coverage import CoverageIncompleteError, CoverageVerifier
from lineage_api.application.models import CoverageManifest, CoverageState


def manifest(**changes: object) -> CoverageManifest:
    values: dict[str, object] = {
        "manifest_id": "coverage-001",
        "workflow_kind": "INCREMENTAL",
        "scope": "repo:payments-pipeline",
        "artifact_digest": "sha256:source-v2",
        "determinant_digest": "sha256:determinants-v1",
        "state": CoverageState.COMPLETE,
        "expected_scope": ("pipeline.py", "models/revenue.sql", "models/legacy.sql"),
        "completed_scope": ("pipeline.py",),
        "reused_scope": ("models/revenue.sql",),
        "skipped_scope": ("models/legacy.sql",),
    }
    values.update(changes)
    return CoverageManifest(**values)


def test_complete_coverage_requires_exactly_once_accounting() -> None:
    verifier = CoverageVerifier()

    result = verifier.verify(manifest())

    assert result.ready is True
    assert result.errors == ()
    assert result.accounted_scope == 3
    assert result.expected_scope == 3


def test_missing_duplicate_or_forbidden_scope_cannot_complete() -> None:
    verifier = CoverageVerifier()
    invalid = manifest(
        reused_scope=("models/revenue.sql", "pipeline.py"),
        skipped_scope=(),
        failed_scope=("models/legacy.sql",),
    )

    result = verifier.verify(invalid)

    assert result.ready is False
    assert set(result.errors) == {
        "scope is accounted more than once: pipeline.py",
        "COMPLETE coverage contains failed scope",
    }
    with pytest.raises(CoverageIncompleteError, match="accounted more than once"):
        verifier.require_complete(invalid)


def test_planned_and_explicitly_incomplete_manifests_are_never_release_ready() -> None:
    verifier = CoverageVerifier()

    planned = verifier.verify(manifest(state=CoverageState.PLANNED))
    incomplete = verifier.verify(
        manifest(
            state=CoverageState.INCOMPLETE,
            completed_scope=(),
            reused_scope=(),
            skipped_scope=(),
        )
    )

    assert planned.ready is False
    assert planned.errors == ("coverage state is not COMPLETE: PLANNED",)
    assert incomplete.ready is False
    assert "scope is unaccounted: pipeline.py" in incomplete.errors
