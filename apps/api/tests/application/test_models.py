from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest


def _models():
    try:
        from lineage_api.application.models import (
            CoverageManifest,
            CoverageState,
            Lease,
            StageIdentity,
            parse_utc,
        )
    except ModuleNotFoundError:
        pytest.fail("Application models are not implemented")
    return CoverageManifest, CoverageState, Lease, StageIdentity, parse_utc


def test_stage_identity_key_is_deterministic_and_changes_with_each_determinant() -> None:
    _, _, _, StageIdentity, _ = _models()
    base = StageIdentity(
        workflow_kind="INCREMENTAL",
        scope="repo:payments-pipeline",
        artifact_digest="sha256:source-v2",
        stage_name="ANALYZE",
        determinant_digest="sha256:determinants-v1",
        schema_version="1.0.0",
    )

    assert base.idempotency_key() == replace(base).idempotency_key()
    assert base.idempotency_key().startswith("stage:sha256:")
    for field, changed in (
        ("workflow_kind", "BASELINE"),
        ("scope", "repo:orders"),
        ("artifact_digest", "sha256:source-v3"),
        ("stage_name", "CONSOLIDATE"),
        ("determinant_digest", "sha256:determinants-v2"),
        ("schema_version", "2.0.0"),
    ):
        assert base.idempotency_key() != replace(base, **{field: changed}).idempotency_key()


def test_lease_is_immutable_uses_utc_and_expires_at_the_boundary() -> None:
    _, _, Lease, _, parse_utc = _models()
    expiry = parse_utc("2026-08-05T12:00:30Z")
    lease = Lease(command_id="command-001", owner="worker-a", epoch=1, expires_at=expiry)

    assert lease.is_expired(expiry - timedelta(microseconds=1)) is False
    assert lease.is_expired(expiry) is True
    assert expiry.tzinfo is UTC
    with pytest.raises(FrozenInstanceError):
        lease.epoch = 2
    with pytest.raises(ValueError, match="timezone"):
        parse_utc("2026-08-05T12:00:30")
    with pytest.raises(ValueError, match="positive"):
        Lease(command_id="command-001", owner="worker-a", epoch=0, expires_at=expiry)


def test_terminal_coverage_reports_missing_duplicates_and_forbidden_complete_scope() -> None:
    CoverageManifest, CoverageState, _, _, _ = _models()
    manifest = CoverageManifest(
        manifest_id="coverage-001",
        workflow_kind="INCREMENTAL",
        scope="repo:payments-pipeline",
        artifact_digest="sha256:source-v2",
        determinant_digest="sha256:determinants-v1",
        state=CoverageState.COMPLETE,
        expected_scope=("pipeline.py", "models/revenue.sql"),
        completed_scope=("pipeline.py",),
        reused_scope=("models/revenue.sql",),
    )

    assert manifest.accounting_errors() == ()
    assert manifest.is_complete is True
    invalid = replace(
        manifest,
        completed_scope=(),
        reused_scope=("models/revenue.sql", "pipeline.py"),
        failed_scope=("pipeline.py",),
    )
    assert set(invalid.accounting_errors()) == {
        "scope is accounted more than once: pipeline.py",
        "COMPLETE coverage contains failed scope",
    }
    assert invalid.is_complete is False


def test_planned_coverage_is_not_complete_before_terminal_accounting() -> None:
    CoverageManifest, CoverageState, _, _, _ = _models()
    planned = CoverageManifest(
        manifest_id="coverage-001",
        workflow_kind="BASELINE",
        scope="system:payments",
        artifact_digest="sha256:source-v2",
        determinant_digest="sha256:determinants-v1",
        state=CoverageState.PLANNED,
        expected_scope=("payments-pipeline",),
    )

    assert planned.accounting_errors() == ()
    assert planned.is_complete is False


def test_clock_port_accepts_a_structural_implementation() -> None:
    try:
        from lineage_api.application.ports import ClockPort
    except ModuleNotFoundError:
        pytest.fail("Application ports are not implemented")

    class FixedClock:
        def now(self) -> datetime:
            return datetime(2026, 8, 5, 12, tzinfo=UTC)

    assert isinstance(FixedClock(), ClockPort)
