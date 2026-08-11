from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest

from lineage_api.application.pr_gate_execution import PrGateStageUseCase
from lineage_api.application.stage_execution import StageExecutionContext
from lineage_api.application.stage_handlers import production_stage_use_cases


_STAGES = {
    "P1": ("control-stage", "PIN_SIGNED_PR_HEAD_AND_POLICY"),
    "P2": ("control-stage", "FETCH_OR_BUILD_EXACT_CANDIDATE"),
    "P3": ("control-stage", "PIN_DEPLOYED_ARTIFACT_AND_GRAPH"),
    "P4": ("consolidation", "ANALYZE_RELEVANT_CHANGED_PATHS"),
    "P5": ("coverage", "RUN_BOUNDED_IMPACT_TRAVERSAL"),
    "P6": ("control-stage", "EVALUATE_VERSIONED_GATE_POLICY"),
    "P7": ("control-stage", "RECHECK_HEAD_AND_ENVIRONMENT_POINTER"),
    "P8": ("control-stage", "UPSERT_STABLE_GITHUB_CHECK"),
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
        workflow_kind="PR_GATE",
        workflow_version="1.0.0",
        stage_id=stage_id,
        stage_name=name,
        command_id="cmd-pr-42",
        correlation_id="corr-pr-42",
        input_reference=_reference(f"pr/{stage_id}.json", "a"),
    )


def _event() -> dict[str, object]:
    return {
        "eventId": "github-pr-42-head-abc",
        "eventType": "PULL_REQUEST",
        "provider": "github",
        "providerSequence": 17,
        "repository": "payments-pipeline",
        "prNumber": 42,
        "headSha": "head-abc",
        "targetEnvironment": "staging",
        "system": "payments",
        "candidateArtifactDigest": "sha256:candidate-v2",
        "acceptedAt": "2026-08-08T16:00:00Z",
        "deadlineAt": "2026-08-08T16:02:00Z",
        "correlationId": "corr-pr-42",
    }


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _candidate() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "pr-candidate-analysis",
        "headSha": "head-abc",
        "candidateArtifactDigest": "sha256:candidate-v2",
        "changes": [
            {
                "changeType": "COLUMN_DROP",
                "subject": "urn:ldp:staging:snowflake:payments:raw.transactions#amount",
                "evidenceMechanisms": ["SCA"],
                "changedPaths": ["pipeline.py"],
            }
        ],
        "coverage": {
            "expectedScope": ["pipeline.py"],
            "completedScope": ["pipeline.py"],
            "reusedScope": [],
            "skippedScope": [],
            "unsupportedScope": [],
            "quarantinedScope": [],
            "failedScope": [],
        },
    }


class Artifacts:
    def __init__(self) -> None:
        event = _event()
        self.documents = {
            "pr/auth.json": {
                "schemaVersion": "1.0.0",
                "artifactType": "pr-authentication-receipt",
                "decision": "AUTHENTICATED",
                "eventDigest": _digest(event),
                "provider": "github",
                "principal": "github-app:lineage",
                "verifiedAt": "2026-08-08T16:00:00Z",
            },
            "pr/candidate.json": _candidate(),
        }

    def get(self, reference: object) -> object:
        return deepcopy(self.documents[reference["key"]])

    def put(self, *_args: object) -> object:
        raise AssertionError("PR gate stages may not write secondary lineage artifacts")


class Control:
    def __init__(self) -> None:
        self.current_head = "head-abc"
        self.checks: dict[str, dict[str, Any]] = {}
        self.pin_calls: list[dict[str, Any]] = []

    def pin_pr_head(
        self,
        event: dict[str, Any],
        event_digest: str,
        policy_version: str,
        cohort_version: str,
    ) -> dict[str, Any]:
        self.pin_calls.append(deepcopy(event))
        self.current_head = event["headSha"]
        return {
            "disposition": "CURRENT",
            "headSha": self.current_head,
            "providerSequence": event["providerSequence"],
            "eventDigest": event_digest,
            "policyVersion": policy_version,
            "cohortVersion": cohort_version,
        }

    def current_pr_head(self, repository: str, pr_number: int) -> dict[str, Any] | None:
        return {
            "repository": repository,
            "prNumber": pr_number,
            "headSha": self.current_head,
            "providerSequence": 17,
        }

    def active_pointer(self, environment: str) -> dict[str, Any]:
        return {
            "environment": environment,
            "graphVersion": "graph-v1",
            "graphChecksum": "b" * 64,
            "fence": 7,
            "package": _reference("packages/payments-v1.json", "b"),
            "correlationId": "corr-deploy",
        }

    def deployment_state(
        self, system: str, environment: str
    ) -> dict[str, Any] | None:
        return {
            "system": system,
            "environment": environment,
            "terminalOutcome": "PROMOTED",
            "deployedArtifactDigest": "sha256:deployed-v1",
            "graphVersion": "graph-v1",
        }

    def upsert_pr_check(self, check: dict[str, Any]) -> dict[str, Any]:
        self.checks[check["checkId"]] = deepcopy(check)
        return deepcopy(check)


class Projection:
    def __init__(self, *, block: int = 0, truncated: bool = False) -> None:
        self.block = block
        self.truncated = truncated
        self.calls: list[dict[str, Any]] = []

    def impact(
        self,
        namespace: str,
        subject: str,
        change_type: str,
        *,
        depth: int,
        limit: int,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "namespace": namespace,
                "subject": subject,
                "changeType": change_type,
                "depth": depth,
                "limit": limit,
            }
        )
        affected = []
        if self.block:
            affected = [
                {
                    "urn": "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue",
                    "severity": "BLOCK",
                    "band": "HIGH",
                    "pathLength": 1,
                    "viaEdges": ["edge-1"],
                }
            ]
        return {
            "namespaceVersion": namespace,
            "subject": subject,
            "changeType": change_type,
            "depthSearched": depth,
            "truncated": self.truncated,
            "affected": affected,
            "summary": {"block": self.block, "warn": 0, "info": 0},
        }


def _intent() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "pr-gate-intent",
        "event": _event(),
        "authenticationRef": _reference("pr/auth.json", "c"),
        "candidateAnalysisRef": _reference("pr/candidate.json", "d"),
        "policyVersion": "pr-policy-v1",
        "cohortVersion": "enforce-v1",
        "impactDepth": 5,
        "impactLimit": 500,
    }


def _execute(
    use_case: PrGateStageUseCase,
    *,
    before_stage: dict[str, Any] | None = None,
) -> list[object]:
    document: object = _intent()
    results = []
    for stage_id in _STAGES:
        if before_stage is not None and before_stage.get("stageId") == stage_id:
            before_stage["action"]()
        result = use_case.execute(document, _context(stage_id))
        results.append(result)
        document = result.document
    return results


def test_pr_gate_stages_pin_read_only_facts_and_emit_one_stable_pass_check() -> None:
    artifacts = Artifacts()
    control = Control()
    projection = Projection()
    results = _execute(PrGateStageUseCase(artifacts, control, projection))

    assert [result.artifact_kind for result in results] == [
        "pr-head-pin",
        "pr-candidate-pin",
        "pr-environment-pin",
        "pr-analysis",
        "pr-impact",
        "pr-policy-decision",
        "pr-freshness-decision",
        "pr-gate-check",
    ]
    final = results[-1].document
    assert final["terminalOutcome"] == "PASS"
    assert final["verdict"] == "PASS"
    assert final["headSha"] == "head-abc"
    assert final["environmentVersion"] == "graph-v1"
    assert final["environmentFence"] == 7
    assert final["deployedArtifactDigest"] == "sha256:deployed-v1"
    assert final["candidateArtifactDigest"] == "sha256:candidate-v2"
    assert final["reasons"] == []
    assert final["checkId"] in control.checks
    assert len(control.checks) == 1
    assert projection.calls[0]["depth"] == 5


def test_pr_gate_never_passes_truncated_impact_or_a_force_pushed_head() -> None:
    truncated = _execute(
        PrGateStageUseCase(Artifacts(), Control(), Projection(truncated=True))
    )[-1].document
    control = Control()
    force_pushed = _execute(
        PrGateStageUseCase(Artifacts(), control, Projection()),
        before_stage={
            "stageId": "P7",
            "action": lambda: setattr(control, "current_head", "head-new"),
        },
    )[-1].document

    assert truncated["terminalOutcome"] == "WARN"
    assert "IMPACT_TRUNCATED" in truncated["reasons"]
    assert force_pushed["terminalOutcome"] == "WARN"
    assert "PR_HEAD_SUPERSEDED" in force_pushed["reasons"]


def test_pr_gate_blocks_a_calibrated_static_violation() -> None:
    final = _execute(
        PrGateStageUseCase(Artifacts(), Control(), Projection(block=1))
    )[-1].document

    assert final["terminalOutcome"] == "BLOCK"
    assert final["verdict"] == "BLOCK"


def test_pr_gate_rejects_llm_evidence_before_projection_io() -> None:
    artifacts = Artifacts()
    artifacts.documents["pr/candidate.json"]["changes"][0]["evidenceMechanisms"] = [
        "LLM"
    ]
    projection = Projection()
    use_case = PrGateStageUseCase(artifacts, Control(), projection)
    document: object = _intent()

    for stage_id in ("P1", "P2", "P3"):
        document = use_case.execute(document, _context(stage_id)).document
    with pytest.raises(ValueError, match="LLM"):
        use_case.execute(document, _context("P4"))

    assert projection.calls == []


def test_pr_gate_rejects_an_inconsistent_graph_impact_result() -> None:
    class InconsistentProjection(Projection):
        def __init__(self, mismatch: str) -> None:
            super().__init__()
            self.mismatch = mismatch

        def impact(self, *args: object, **kwargs: object) -> dict[str, Any]:
            result = super().impact(*args, **kwargs)
            if self.mismatch == "subject":
                result["subject"] = "urn:ldp:staging:snowflake:other:wrong.table"
            else:
                result["summary"]["block"] = 1
            return result

    for mismatch in ("subject", "summary"):
        with pytest.raises(ValueError, match="impact"):
            _execute(
                PrGateStageUseCase(
                    Artifacts(), Control(), InconsistentProjection(mismatch)
                )
            )


def test_pr_gate_bounds_affected_results_across_all_changes() -> None:
    class OneImpactPerCall(Projection):
        def impact(
            self,
            namespace: str,
            subject: str,
            change_type: str,
            *,
            depth: int,
            limit: int,
        ) -> dict[str, Any]:
            result = super().impact(
                namespace, subject, change_type, depth=depth, limit=limit
            )
            result["affected"] = [
                {
                    "urn": "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue",
                    "severity": "INFO",
                    "band": "LOWEST",
                    "pathLength": 1,
                    "viaEdges": [f"edge-{len(self.calls)}"],
                }
            ]
            result["summary"]["info"] = 1
            return result

    artifacts = Artifacts()
    artifacts.documents["pr/candidate.json"]["changes"].append(
        {
            "changeType": "COLUMN_DROP",
            "subject": "urn:ldp:staging:snowflake:payments:raw.transactions#currency",
            "evidenceMechanisms": ["SCA"],
            "changedPaths": ["pipeline.py"],
        }
    )
    projection = OneImpactPerCall()
    use_case = PrGateStageUseCase(
        artifacts, Control(), projection, max_affected=1
    )
    document: object = {**_intent(), "impactLimit": 1}
    for stage_id in _STAGES:
        document = use_case.execute(document, _context(stage_id)).document

    assert document["terminalOutcome"] == "WARN"
    assert "IMPACT_TRUNCATED" in document["reasons"]
    assert len(projection.calls) == 1


def test_production_dispatcher_maps_all_pr_gate_states() -> None:
    artifacts = Artifacts()
    control = Control()
    projection = Projection()
    use_cases = production_stage_use_cases(
        artifacts,
        control,
        packages=artifacts,
        publication_control=control,
        projection=projection,
    )

    assert all(("PR_GATE", stage_id) in use_cases for stage_id in _STAGES)
    assert all(
        isinstance(use_cases[("PR_GATE", stage_id)], PrGateStageUseCase)
        for stage_id in _STAGES
    )
