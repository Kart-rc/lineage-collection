from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest

from lineage_api.application.consolidation import edge_key_for
from lineage_api.application.nightly_execution import NightlyStageUseCase
from lineage_api.application.stage_execution import StageExecutionContext
from lineage_api.application.stage_handlers import production_stage_use_cases
from lineage_api.application.stage_ownership import WORKFLOWS


SOURCE = "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
TARGET = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue"
EDGE_ID = edge_key_for([SOURCE], TARGET, "DERIVES")
_STAGES = {
    "N1": ("control-stage", "RECONCILE_EVENTS_RECEIPTS_AND_ARCHIVES"),
    "N3": ("publication", "VERIFY_PROJECTION_CHECKSUMS"),
    "N4": ("control-stage", "DRAIN_BOUNDED_STALE_LLM_CACHE"),
    "N5": ("control-stage", "EVALUATE_AUTOPUBLISH_AUDIT_SAMPLE"),
    "N6": ("proposal", "EMIT_PROPOSALS_ALERTS_AND_EVIDENCE"),
}


def _reference(key: str, digit: str) -> dict[str, object]:
    return {
        "bucket": "evidence",
        "key": key,
        "versionId": f"{digit}-v1",
        "sha256": digit * 64,
        "sizeBytes": 2048,
    }


def _context(stage_id: str) -> StageExecutionContext:
    target, name = _STAGES[stage_id]
    return StageExecutionContext(
        target=target,
        workflow_kind="NIGHTLY",
        workflow_version="1.0.0",
        stage_id=stage_id,
        stage_name=name,
        command_id="cmd-nightly-1",
        correlation_id="corr-nightly-1",
        input_reference=_reference(f"nightly/{stage_id}.json", "a"),
    )


def _checksum(rows: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _assertion(reference: dict[str, object]) -> dict[str, object]:
    return {
        "provenanceId": "sca-nightly-1",
        "from": [SOURCE],
        "to": TARGET,
        "edgeType": "DERIVES",
        "transform": "SUM(amount)",
        "mechanism": "SCA",
        "exact": True,
        "evidenceRef": reference,
        "repo": "payments-pipeline",
        "runId": "nightly-run-1",
        "correlationId": "corr-nightly-1",
        "sessionComplete": True,
        "citation": {"file": "pipeline.py", "line": 7, "astPath": "Module.Assign"},
    }


class Artifacts:
    def __init__(self, *, reconciled: bool = False, audit_healthy: bool = False) -> None:
        self.writes: dict[str, object] = {}
        evidence_ref = _reference("nightly/evidence.json", "1")
        accepted = ["event-1"]
        self.documents: dict[str, object] = {
            "nightly/reconciliation.json": {
                "schemaVersion": "1.0.0",
                "artifactType": "nightly-event-reconciliation-snapshot",
                "runId": "nightly-run-1",
                "correlationId": "corr-nightly-1",
                "acceptedEventIds": accepted,
                "receiptEventIds": accepted if reconciled else [],
                "archivedEventIds": accepted if reconciled else [],
            },
            "nightly/cache.json": {
                "schemaVersion": "1.0.0",
                "artifactType": "stale-llm-cache-manifest",
                "runId": "nightly-run-1",
                "entries": []
                if reconciled
                else [{"cacheKey": "llm-cache-1", "determinantDigest": "d" * 64}],
            },
            "nightly/audit.json": {
                "schemaVersion": "1.0.0",
                "artifactType": "autopublish-audit-sample",
                "runId": "nightly-run-1",
                "samples": [
                    {
                        "proposalId": "proposal-1",
                        "decision": "AUTO_PUBLISHED",
                        "coverageState": "COMPLETE" if audit_healthy else "INCOMPLETE",
                        "mechanisms": ["SCA"],
                        "staticExact": True,
                    }
                ],
            },
            "nightly/assertions.json": {
                "schemaVersion": "1.0.0",
                "artifactType": "assertion-set",
                "workUnitId": "nightly-run-1",
                "assertions": [_assertion(evidence_ref)],
            },
        }

    def get(self, reference: object) -> object:
        assert isinstance(reference, dict)
        return deepcopy(self.documents[str(reference["key"])])

    def put(
        self, kind: str, key: str, body: object, _schema_version: str
    ) -> dict[str, object]:
        self.documents[key] = deepcopy(body)
        self.writes[key] = deepcopy(body)
        return _reference(key, "9")


class Control:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.pointer = {
            "environment": "staging",
            "graphVersion": "graph-v1",
            "graphChecksum": _checksum(rows),
            "fence": 7,
            "correlationId": "corr-publish",
        }
        self.invalidations: list[dict[str, str]] = []
        self.report: dict[str, Any] | None = None
        self.proposal: dict[str, Any] | None = None

    def active_pointer(self, _environment: str) -> dict[str, Any]:
        return deepcopy(self.pointer)

    def invalidate_llm_cache(
        self, cache_key: str, determinant_digest: str, run_id: str
    ) -> dict[str, str]:
        result = {
            "cacheKey": cache_key,
            "determinantDigest": determinant_digest,
            "runId": run_id,
            "disposition": "INVALIDATED",
        }
        self.invalidations.append(result)
        return deepcopy(result)

    def complete_nightly(
        self, report: dict[str, Any], proposal: dict[str, Any] | None
    ) -> dict[str, Any]:
        self.report = deepcopy(report)
        self.proposal = deepcopy(proposal)
        return deepcopy(report)


class Projection:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.reads: list[str] = []

    def namespace_checksum(self, namespace: str) -> list[dict[str, str]]:
        self.reads.append(namespace)
        return deepcopy(self.rows)


def _intent() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "nightly-reconciliation-intent",
        "runId": "nightly-run-1",
        "repository": "payments-pipeline",
        "artifactDigest": "sha256:source-v1",
        "environment": "staging",
        "platform": "snowflake",
        "system": "payments",
        "repositorySource": _reference("source/payments.zip", "2"),
        "catalogSnapshotRef": _reference("catalog/payments.json", "3"),
        "catalogSnapshotId": "catalog-v1",
        "resolverVersion": "resolver-v1",
        "rulesetVersion": "rules-v1",
        "acceptedAt": "2026-08-08T03:00:00Z",
        "samplePaths": ["pipeline.py"],
        "sampleScopeUrns": [TARGET, SOURCE],
        "eventReconciliationRef": _reference("nightly/reconciliation.json", "4"),
        "staleCacheRef": _reference("nightly/cache.json", "5"),
        "auditSampleRef": _reference("nightly/audit.json", "6"),
        "cacheDrainLimit": 50,
    }


def _sca_result(work_unit: dict[str, Any]) -> dict[str, Any]:
    context = {
        name: deepcopy(work_unit[name])
        for name in (
            "repository",
            "artifactDigest",
            "environment",
            "platform",
            "system",
            "repositorySource",
            "catalogSnapshotRef",
            "catalogSnapshotId",
            "resolverVersion",
            "rulesetVersion",
            "activeBaseVersion",
            "activeBaseFence",
            "acceptedAt",
            "coverage",
            "nightly",
        )
    }
    context["coverage"]["completedScope"] = ["pipeline.py"]
    context["assertionRefs"] = [_reference("nightly/assertions.json", "7")]
    context["residueRefs"] = []
    context["runtimeManifestRefs"] = []
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "sca-stage-result",
        "workflowKind": "NIGHTLY",
        "workflowVersion": "1.0.0",
        "stageId": "N2",
        "stageName": "SAMPLE_PUBLISHED_LINEAGE_AGAINST_CLEAN_ANALYSIS",
        "commandId": "cmd-nightly-1",
        "correlationId": "corr-nightly-1",
        "source": _reference("nightly/N2-input.json", "8"),
        "context": context,
    }


def _run(
    artifacts: Artifacts, control: Control, projection: Projection
) -> tuple[list[object], dict[str, Any]]:
    use_case = NightlyStageUseCase(artifacts, control, projection)
    n1 = use_case.execute(_intent(), _context("N1"))
    document: object = _sca_result(n1.document)
    results = [n1]
    for stage_id in ("N3", "N4", "N5", "N6"):
        result = use_case.execute(document, _context(stage_id))
        results.append(result)
        document = result.document
    assert isinstance(document, dict)
    return results, document


def test_nightly_chain_raises_a_reference_based_proposal_and_alerts_for_drift() -> None:
    artifacts = Artifacts()
    projection = Projection([])
    control = Control(projection.rows)

    results, final = _run(artifacts, control, projection)

    assert [result.artifact_kind for result in results] == [
        "sca-work-unit",
        "nightly-projection-verification",
        "nightly-cache-drain",
        "nightly-audit-decision",
        "nightly-reconciliation-result",
    ]
    assert final["terminalOutcome"] == "PROPOSALS_RAISED"
    assert set(final["alerts"]) == {
        "ARCHIVE_RECONCILIATION_DRIFT",
        "AUTOPUBLISH_AUDIT_DRIFT",
        "PUBLISHED_SAMPLE_DRIFT",
    }
    assert control.proposal is not None
    assert control.proposal["proposalType"] == "RECONCILIATION"
    assert control.proposal["diff"]["addedEdgeIds"] == [EDGE_ID]
    assert control.proposal["diff"]["removedEdgeIds"] == []
    assert control.invalidations[0]["cacheKey"] == "llm-cache-1"
    assert projection.reads == ["graph-v1"]
    assert control.report == final


def test_nightly_chain_is_an_audited_noop_when_every_pin_and_sample_matches() -> None:
    rows = [{"edgeId": EDGE_ID, "source": SOURCE, "target": TARGET, "type": "DERIVES"}]
    artifacts = Artifacts(reconciled=True, audit_healthy=True)
    projection = Projection(rows)
    control = Control(rows)

    _, final = _run(artifacts, control, projection)

    assert final["terminalOutcome"] == "RECONCILED"
    assert final["alerts"] == []
    assert control.proposal is None
    assert control.invalidations == []


def test_nightly_rejects_a_cache_manifest_from_another_run_before_invalidation() -> None:
    artifacts = Artifacts()
    artifacts.documents["nightly/cache.json"]["runId"] = "another-run"
    projection = Projection([])
    control = Control([])
    use_case = NightlyStageUseCase(artifacts, control, projection)
    work_unit = use_case.execute(_intent(), _context("N1")).document
    n3 = use_case.execute(_sca_result(work_unit), _context("N3")).document

    with pytest.raises(ValueError, match="stale cache manifest"):
        use_case.execute(n3, _context("N4"))

    assert control.invalidations == []


def test_nightly_rejects_a_prior_stage_artifact_from_another_command() -> None:
    artifacts = Artifacts()
    projection = Projection([])
    control = Control([])
    use_case = NightlyStageUseCase(artifacts, control, projection)
    work_unit = use_case.execute(_intent(), _context("N1")).document
    sca_result = _sca_result(work_unit)
    sca_result["commandId"] = "another-command"

    with pytest.raises(ValueError, match="prior artifact is not bound"):
        use_case.execute(sca_result, _context("N3"))

    assert projection.reads == []


def test_production_dispatcher_maps_the_complete_nightly_chain() -> None:
    artifacts = Artifacts()
    control = Control([])
    projection = Projection([])
    use_cases = production_stage_use_cases(
        artifacts,
        control,
        packages=artifacts,
        publication_control=control,
        projection=projection,
        sources=object(),
    )

    assert all(("NIGHTLY", f"N{index}") in use_cases for index in range(1, 7))
    assert all(
        isinstance(use_cases[("NIGHTLY", stage)], NightlyStageUseCase)
        for stage in ("N1", "N3", "N4", "N5", "N6")
    )
    expected = {
        (workflow_kind, stage.stage_id)
        for workflow_kind, workflow in WORKFLOWS.items()
        for stage in workflow.stages
    }
    assert expected <= set(use_cases)
