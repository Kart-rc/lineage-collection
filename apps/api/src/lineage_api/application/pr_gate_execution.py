from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping

from lineage_api.application.ports import (
    ArtifactStorePort,
    ImpactProjectionPort,
    PrGateControlPort,
)
from lineage_api.application.stage_execution import (
    StageExecutionContext,
    StageExecutionResult,
    validate_artifact_reference,
)
from lineage_api.application.workflows.pr_gate import CHANGE_TYPES
from lineage_api.domain.impact import BAND_ORDER, severity_for
from lineage_api.domain.urns import LineageUrn


_STAGES = {
    "P1": ("pr-head-pin", None),
    "P2": ("pr-candidate-pin", "pr-head-pin"),
    "P3": ("pr-environment-pin", "pr-candidate-pin"),
    "P4": ("pr-analysis", "pr-environment-pin"),
    "P5": ("pr-impact", "pr-analysis"),
    "P6": ("pr-policy-decision", "pr-impact"),
    "P7": ("pr-freshness-decision", "pr-policy-decision"),
    "P8": ("pr-gate-check", "pr-freshness-decision"),
}
_MECHANISMS = frozenset({"SCA", "NATIVE", "MANIFEST", "CACHE"})
_COVERAGE_FIELDS = (
    "completedScope",
    "reusedScope",
    "skippedScope",
    "unsupportedScope",
    "quarantinedScope",
    "failedScope",
)


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _text(name: str, value: object, *, limit: int = 2_048) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit:
        raise ValueError(f"invalid {name}")
    return value


def _integer(name: str, value: object, *, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"invalid {name}")
    return value


def _timestamp(name: str, value: object) -> tuple[str, datetime]:
    text = _text(name, value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid {name}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"invalid {name}")
    normalized = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return normalized, parsed.astimezone(UTC)


def _path(value: object) -> str:
    text = _text("PR changed path", value, limit=1_024)
    parsed = PurePosixPath(text)
    if text.startswith("/") or "\\" in text or ".." in parsed.parts or text == ".":
        raise ValueError("PR paths must be normalized and relative")
    return parsed.as_posix()


def _paths(name: str, value: object, *, limit: int = 10_000) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{name} must be a bounded array")
    normalized = [_path(item) for item in value]
    if normalized != sorted(set(normalized)):
        raise ValueError(f"{name} must be sorted and unique")
    return normalized


def _reasons(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("PR gate reasons must be a bounded array")
    reasons = [_text("PR gate reason", item, limit=128) for item in value]
    if len(reasons) != len(set(reasons)):
        raise ValueError("PR gate reasons must be unique")
    return reasons


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


class PrGateStageUseCase:
    def __init__(
        self,
        artifacts: ArtifactStorePort,
        control: PrGateControlPort,
        projection: ImpactProjectionPort,
        *,
        max_changes: int = 100,
        max_affected: int = 1_000,
    ) -> None:
        if not 1 <= max_changes <= 1_000 or not 1 <= max_affected <= 10_000:
            raise ValueError("PR gate bounds are invalid")
        self._artifacts = artifacts
        self._control = control
        self._projection = projection
        self._max_changes = max_changes
        self._max_affected = max_affected

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if context.workflow_kind != "PR_GATE" or context.stage_id not in _STAGES:
            raise ValueError("PR gate use case received an unsupported stage")
        if context.stage_id == "P1":
            return self._pin_head(input_document, context)
        document, gate, reasons = self._prior(input_document, context.stage_id)
        if context.stage_id == "P2":
            return self._candidate(document, gate, reasons, context)
        if context.stage_id == "P3":
            return self._environment(document, gate, reasons, context)
        if context.stage_id == "P4":
            return self._analysis(document, gate, reasons, context)
        if context.stage_id == "P5":
            return self._impact(document, gate, reasons, context)
        if context.stage_id == "P6":
            return self._policy(document, gate, reasons, context)
        if context.stage_id == "P7":
            return self._freshness(document, gate, reasons, context)
        return self._check(document, gate, reasons, context)

    def _pin_head(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != "pr-gate-intent"
        ):
            raise ValueError("P1 requires a versioned PR gate intent")
        event = self._event(input_document.get("event"), context.correlation_id)
        authentication_ref = validate_artifact_reference(
            input_document.get("authenticationRef")
        )
        candidate_ref = validate_artifact_reference(
            input_document.get("candidateAnalysisRef")
        )
        policy_version = _text("PR policy version", input_document.get("policyVersion"))
        cohort_version = _text("PR cohort version", input_document.get("cohortVersion"))
        impact_depth = _integer(
            "PR impact depth", input_document.get("impactDepth"), minimum=1, maximum=5
        )
        impact_limit = _integer(
            "PR impact limit",
            input_document.get("impactLimit"),
            minimum=1,
            maximum=self._max_affected,
        )
        accepted_at, accepted = _timestamp("PR accepted time", event["acceptedAt"])
        deadline_at, deadline = _timestamp("PR deadline", event["deadlineAt"])
        elapsed = (deadline - accepted).total_seconds()
        if not 0 < elapsed <= 120:
            raise ValueError("PR gate deadline must be within 120 seconds")
        event_digest = _digest(event)
        receipt = self._artifacts.get(authentication_ref)
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("schemaVersion") != "1.0.0"
            or receipt.get("artifactType") != "pr-authentication-receipt"
            or receipt.get("decision") != "AUTHENTICATED"
            or receipt.get("eventDigest") != event_digest
            or receipt.get("provider") != event["provider"]
        ):
            raise ValueError("PR authentication receipt is invalid or unbound")
        _text("PR authenticated principal", receipt.get("principal"))
        _timestamp("PR authentication time", receipt.get("verifiedAt"))
        pin = self._control.pin_pr_head(
            event, event_digest, policy_version, cohort_version
        )
        disposition = pin.get("disposition")
        if disposition not in {"CURRENT", "DUPLICATE", "STALE"}:
            raise ValueError("PR head pin disposition is invalid")
        reasons: list[str] = []
        if disposition == "STALE":
            _add_reason(reasons, "PR_HEAD_SUPERSEDED")
        gate = {
            "repository": event["repository"],
            "prNumber": event["prNumber"],
            "headSha": event["headSha"],
            "provider": event["provider"],
            "providerSequence": event["providerSequence"],
            "system": event["system"],
            "targetEnvironment": event["targetEnvironment"],
            "candidateArtifactDigest": event["candidateArtifactDigest"],
            "acceptedAt": accepted_at,
            "deadlineAt": deadline_at,
            "policyVersion": policy_version,
            "cohortVersion": cohort_version,
            "impactDepth": impact_depth,
            "impactLimit": impact_limit,
            "eventDigest": event_digest,
            "authenticationRef": authentication_ref,
            "candidateAnalysisRef": candidate_ref,
        }
        return self._result(
            context,
            gate,
            reasons,
            {"headPin": dict(pin), "event": event},
        )

    def _candidate(
        self,
        document: Mapping[str, Any],
        gate: dict[str, Any],
        reasons: list[str],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        candidate = self._artifacts.get(gate["candidateAnalysisRef"])
        if (
            not isinstance(candidate, Mapping)
            or candidate.get("schemaVersion") != "1.0.0"
            or candidate.get("artifactType") != "pr-candidate-analysis"
            or candidate.get("headSha") != gate["headSha"]
            or candidate.get("candidateArtifactDigest")
            != gate["candidateArtifactDigest"]
        ):
            raise ValueError("PR candidate analysis is invalid or unbound")
        raw_changes = candidate.get("changes")
        if not isinstance(raw_changes, list) or not 1 <= len(raw_changes) <= self._max_changes:
            raise ValueError("PR candidate changes must be a bounded non-empty array")
        if not isinstance(candidate.get("coverage"), Mapping):
            raise ValueError("PR candidate coverage is missing")
        return self._result(
            context,
            gate,
            reasons,
            {
                "candidate": {
                    "headSha": candidate["headSha"],
                    "candidateArtifactDigest": candidate["candidateArtifactDigest"],
                    "analysisRef": gate["candidateAnalysisRef"],
                },
                "changes": list(raw_changes),
                "coverage": dict(candidate["coverage"]),
            },
        )

    def _environment(
        self,
        document: Mapping[str, Any],
        gate: dict[str, Any],
        reasons: list[str],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        pointer = self._control.active_pointer(gate["targetEnvironment"])
        graph_version = pointer.get("graphVersion")
        fence = pointer.get("fence")
        if (
            not isinstance(graph_version, str)
            or not graph_version
            or graph_version == "NONE"
            or not isinstance(fence, int)
            or isinstance(fence, bool)
            or fence < 0
        ):
            _add_reason(reasons, "MISSING_ENVIRONMENT")
            graph_version = "MISSING"
            fence = 0
        deployment = self._control.deployment_state(
            gate["system"], gate["targetEnvironment"]
        )
        deployed_digest = None
        if isinstance(deployment, Mapping):
            deployed_digest = deployment.get("deployedArtifactDigest")
        if (
            not isinstance(deployment, Mapping)
            or deployment.get("terminalOutcome") not in {"PROMOTED", "ROLLED_BACK"}
            or deployment.get("graphVersion") != graph_version
            or not isinstance(deployed_digest, str)
            or not deployed_digest
        ):
            _add_reason(reasons, "LINEAGE_OUT_OF_SYNC")
        package = pointer.get("package")
        package_ref = (
            validate_artifact_reference(package) if package is not None else None
        )
        gate.update(
            environmentVersion=graph_version,
            environmentFence=fence,
            deployedArtifactDigest=deployed_digest,
            environmentPackageRef=package_ref,
            pointerCorrelationId=pointer.get("correlationId"),
        )
        return self._result(
            context,
            gate,
            reasons,
            {
                "candidate": document["candidate"],
                "changes": document["changes"],
                "coverage": document["coverage"],
                "environmentPin": {
                    "graphVersion": graph_version,
                    "fence": fence,
                    "deployedArtifactDigest": deployed_digest,
                    "packageRef": package_ref,
                },
            },
        )

    def _analysis(
        self,
        document: Mapping[str, Any],
        gate: dict[str, Any],
        reasons: list[str],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        raw_changes = document.get("changes")
        if not isinstance(raw_changes, list) or not 1 <= len(raw_changes) <= self._max_changes:
            raise ValueError("PR analysis changes are invalid")
        changes = [self._change(item) for item in raw_changes]
        coverage = self._coverage(document.get("coverage"))
        if coverage["state"] != "COMPLETE":
            _add_reason(reasons, "INCOMPLETE_COVERAGE")
        return self._result(
            context,
            gate,
            reasons,
            {"changes": changes, "coverage": coverage},
        )

    def _impact(
        self,
        document: Mapping[str, Any],
        gate: dict[str, Any],
        reasons: list[str],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        impacts: list[dict[str, Any]] = []
        calibrated_block = False
        remaining = self._max_affected
        for change in document["changes"]:
            if remaining == 0:
                normalized = {
                    "namespaceVersion": gate["environmentVersion"],
                    "subject": change["subject"],
                    "changeType": change["changeType"],
                    "depthSearched": gate["impactDepth"],
                    "truncated": True,
                    "affected": [],
                    "summary": {"block": 0, "warn": 0, "info": 0},
                }
            else:
                result_limit = min(gate["impactLimit"], remaining)
                impact = self._projection.impact(
                    gate["environmentVersion"],
                    change["subject"],
                    change["changeType"],
                    depth=gate["impactDepth"],
                    limit=result_limit,
                )
                normalized = self._impact_result(
                    impact, gate, change, maximum_affected=result_limit
                )
                remaining -= len(normalized["affected"])
            if normalized["truncated"]:
                _add_reason(reasons, "IMPACT_TRUNCATED")
            if normalized["summary"]["warn"] > 0:
                _add_reason(reasons, "IMPACT_WARNING")
            calibrated_block = calibrated_block or normalized["summary"]["block"] > 0
            impacts.append(normalized)
        return self._result(
            context,
            gate,
            reasons,
            {
                "changes": document["changes"],
                "coverage": document["coverage"],
                "impacts": impacts,
                "calibratedBlock": calibrated_block,
            },
        )

    def _policy(
        self,
        document: Mapping[str, Any],
        gate: dict[str, Any],
        reasons: list[str],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        calibrated = document.get("calibratedBlock") is True
        verdict = "BLOCK" if calibrated else ("WARN" if reasons else "PASS")
        policy_input = {
            "policyVersion": gate["policyVersion"],
            "cohortVersion": gate["cohortVersion"],
            "coverage": document["coverage"],
            "impacts": document["impacts"],
            "reasons": reasons,
        }
        decision = {
            "verdict": verdict,
            "reasons": reasons,
            "policyInputChecksum": _digest(policy_input),
        }
        decision["policyOutputChecksum"] = _digest(decision)
        return self._result(
            context,
            gate,
            reasons,
            {
                "changes": document["changes"],
                "coverage": document["coverage"],
                "impacts": document["impacts"],
                "decision": decision,
            },
        )

    def _freshness(
        self,
        document: Mapping[str, Any],
        gate: dict[str, Any],
        reasons: list[str],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        current = self._control.current_pr_head(gate["repository"], gate["prNumber"])
        pointer = self._control.active_pointer(gate["targetEnvironment"])
        stale = False
        if not isinstance(current, Mapping) or current.get("headSha") != gate["headSha"]:
            _add_reason(reasons, "PR_HEAD_SUPERSEDED")
            stale = True
        if (
            pointer.get("graphVersion") != gate["environmentVersion"]
            or pointer.get("fence") != gate["environmentFence"]
        ):
            _add_reason(reasons, "ENVIRONMENT_CHANGED")
            stale = True
        decision = dict(document["decision"])
        decision["reasons"] = reasons
        if stale:
            decision["verdict"] = "WARN"
        decision["freshnessChecksum"] = _digest(
            {
                "head": None if current is None else dict(current),
                "pointer": {
                    "graphVersion": pointer.get("graphVersion"),
                    "fence": pointer.get("fence"),
                },
            }
        )
        return self._result(
            context,
            gate,
            reasons,
            {
                "changes": document["changes"],
                "coverage": document["coverage"],
                "impacts": document["impacts"],
                "decision": decision,
                "freshness": {
                    "headSha": None if current is None else current.get("headSha"),
                    "environmentVersion": pointer.get("graphVersion"),
                    "environmentFence": pointer.get("fence"),
                    "status": "STALE" if stale else "CURRENT",
                },
            },
        )

    def _check(
        self,
        document: Mapping[str, Any],
        gate: dict[str, Any],
        reasons: list[str],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        identity = {
            "repository": gate["repository"],
            "prNumber": gate["prNumber"],
            "headSha": gate["headSha"],
        }
        check_id = f"pr-check-{_digest(identity)[:20]}"
        decision = document["decision"]
        check = {
            "schemaVersion": "1.0.0",
            "checkId": check_id,
            "repository": gate["repository"],
            "prNumber": gate["prNumber"],
            "headSha": gate["headSha"],
            "environment": gate["targetEnvironment"],
            "environmentVersion": gate["environmentVersion"],
            "environmentFence": gate["environmentFence"],
            "deployedArtifactDigest": gate["deployedArtifactDigest"],
            "candidateArtifactDigest": gate["candidateArtifactDigest"],
            "policyVersion": gate["policyVersion"],
            "cohortVersion": gate["cohortVersion"],
            "verdict": decision["verdict"],
            "reasons": reasons,
            "truncated": any(item["truncated"] for item in document["impacts"]),
            "evaluatedChangeTypes": sorted(
                {item["changeType"] for item in document["changes"]}
            ),
            "authenticationRef": gate["authenticationRef"],
            "analysisRef": gate["candidateAnalysisRef"],
            "policyInputChecksum": decision["policyInputChecksum"],
            "policyOutputChecksum": decision["policyOutputChecksum"],
            "freshnessChecksum": decision["freshnessChecksum"],
            "evaluatedAt": gate["acceptedAt"],
            "correlationId": context.correlation_id,
        }
        persisted = self._control.upsert_pr_check(check)
        if persisted != check:
            raise ValueError("stored PR check does not match its deterministic result")
        return self._result(
            context,
            gate,
            reasons,
            {
                **check,
                "terminalOutcome": check["verdict"],
                "providerDelivery": "OUTBOX_PENDING",
            },
        )

    @staticmethod
    def _event(value: object, correlation_id: str) -> dict[str, Any]:
        required = {
            "eventId",
            "eventType",
            "provider",
            "providerSequence",
            "repository",
            "prNumber",
            "headSha",
            "targetEnvironment",
            "system",
            "candidateArtifactDigest",
            "acceptedAt",
            "deadlineAt",
            "correlationId",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("PR event schema is invalid")
        event = dict(value)
        for name in (
            "eventId",
            "provider",
            "repository",
            "headSha",
            "targetEnvironment",
            "system",
            "candidateArtifactDigest",
        ):
            event[name] = _text(f"PR event {name}", event[name])
        if event["eventType"] != "PULL_REQUEST" or event["provider"] != "github":
            raise ValueError("unsupported PR event type or provider")
        event["providerSequence"] = _integer(
            "PR provider sequence", event["providerSequence"], minimum=0, maximum=2**63 - 1
        )
        event["prNumber"] = _integer(
            "PR number", event["prNumber"], minimum=1, maximum=2**31 - 1
        )
        if event["correlationId"] != correlation_id:
            raise ValueError("PR event correlation does not match its command")
        return event

    @staticmethod
    def _change(value: object) -> dict[str, Any]:
        required = {"changeType", "subject", "evidenceMechanisms", "changedPaths"}
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("PR change schema is invalid")
        change_type = _text("PR change type", value["changeType"])
        if change_type not in CHANGE_TYPES:
            raise ValueError("unsupported PR change type")
        subject = _text("PR change subject", value["subject"])
        LineageUrn.parse(subject)
        mechanisms = value["evidenceMechanisms"]
        if not isinstance(mechanisms, list) or not mechanisms:
            raise ValueError("PR change requires evidence mechanisms")
        normalized = [_text("PR evidence mechanism", item, limit=32) for item in mechanisms]
        if normalized != sorted(set(normalized)):
            raise ValueError("PR evidence mechanisms must be sorted and unique")
        if set(normalized) - _MECHANISMS:
            raise ValueError("PR gate forbids LLM or unknown evidence mechanisms")
        return {
            "changeType": change_type,
            "subject": subject,
            "evidenceMechanisms": normalized,
            "changedPaths": _paths("PR changed paths", value["changedPaths"]),
        }

    @staticmethod
    def _coverage(value: object) -> dict[str, Any]:
        allowed = {"expectedScope", *_COVERAGE_FIELDS}
        if not isinstance(value, Mapping) or set(value) != allowed:
            raise ValueError("PR coverage schema is invalid")
        coverage = {
            "expectedScope": _paths("PR expected coverage", value["expectedScope"])
        }
        accounted: list[str] = []
        for field in _COVERAGE_FIELDS:
            coverage[field] = _paths(f"PR {field}", value[field])
            accounted.extend(coverage[field])
        exact = coverage["expectedScope"] == sorted(accounted)
        healthy = exact and not any(
            coverage[name]
            for name in ("unsupportedScope", "quarantinedScope", "failedScope")
        )
        coverage["state"] = "COMPLETE" if healthy else "INCOMPLETE"
        if not exact:
            coverage["reason"] = "SCOPE_ACCOUNTING_MISMATCH"
        return coverage

    def _impact_result(
        self,
        value: object,
        gate: Mapping[str, Any],
        change: Mapping[str, Any],
        *,
        maximum_affected: int,
    ) -> dict[str, Any]:
        required = {
            "namespaceVersion",
            "subject",
            "changeType",
            "depthSearched",
            "truncated",
            "affected",
            "summary",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("PR impact result must be an object")
        if value.get("namespaceVersion") != gate["environmentVersion"]:
            raise ValueError("PR impact namespace does not match its environment pin")
        if (
            value.get("subject") != change["subject"]
            or value.get("changeType") != change["changeType"]
        ):
            raise ValueError("PR impact result does not match its requested change")
        depth = _integer(
            "PR impact result depth",
            value.get("depthSearched"),
            minimum=1,
            maximum=gate["impactDepth"],
        )
        affected = value.get("affected")
        if not isinstance(affected, list) or len(affected) > maximum_affected:
            raise ValueError("PR impact result exceeds its bound")
        normalized_affected = [
            self._affected_item(item, change["changeType"], depth)
            for item in affected
        ]
        if normalized_affected != sorted(
            normalized_affected, key=lambda item: (item["pathLength"], item["urn"])
        ) or len({item["urn"] for item in normalized_affected}) != len(
            normalized_affected
        ):
            raise ValueError("PR impact affected results are not stable and unique")
        summary = value.get("summary")
        if not isinstance(summary, Mapping) or set(summary) != {"block", "warn", "info"}:
            raise ValueError("PR impact summary is invalid")
        normalized_summary = {
            name: _integer(
                f"PR impact {name}", summary[name], minimum=0, maximum=maximum_affected
            )
            for name in ("block", "warn", "info")
        }
        actual_summary = {"block": 0, "warn": 0, "info": 0}
        for item in normalized_affected:
            actual_summary[str(item["severity"]).lower()] += 1
        if normalized_summary != actual_summary:
            raise ValueError("PR impact summary does not match its affected results")
        truncated = value.get("truncated")
        if not isinstance(truncated, bool):
            raise ValueError("PR impact truncation flag is invalid")
        return {
            "namespaceVersion": gate["environmentVersion"],
            "subject": change["subject"],
            "changeType": change["changeType"],
            "depthSearched": depth,
            "truncated": truncated,
            "affected": normalized_affected,
            "summary": normalized_summary,
        }

    @staticmethod
    def _affected_item(
        value: object, change_type: str, maximum_depth: int
    ) -> dict[str, Any]:
        required = {"urn", "severity", "band", "pathLength", "viaEdges"}
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("PR impact affected result schema is invalid")
        urn = _text("PR affected URN", value["urn"])
        LineageUrn.parse(urn)
        band = _text("PR affected evidence band", value["band"], limit=32)
        if band not in BAND_ORDER:
            raise ValueError("PR impact affected evidence band is invalid")
        severity = _text("PR affected severity", value["severity"], limit=16)
        if severity != severity_for(change_type, band):
            raise ValueError("PR impact affected severity is not calibrated")
        path_length = _integer(
            "PR affected path length",
            value["pathLength"],
            minimum=1,
            maximum=maximum_depth,
        )
        via_edges = value["viaEdges"]
        if not isinstance(via_edges, list) or len(via_edges) != path_length:
            raise ValueError("PR impact affected path evidence is invalid")
        normalized_edges = [
            _text("PR impact edge ID", item, limit=512) for item in via_edges
        ]
        if len(set(normalized_edges)) != len(normalized_edges):
            raise ValueError("PR impact path repeats an edge")
        return {
            "urn": urn,
            "severity": severity,
            "band": band,
            "pathLength": path_length,
            "viaEdges": normalized_edges,
        }

    @staticmethod
    def _prior(
        input_document: object, stage_id: str
    ) -> tuple[Mapping[str, Any], dict[str, Any], list[str]]:
        _, expected = _STAGES[stage_id]
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != expected
            or not isinstance(input_document.get("gate"), Mapping)
        ):
            raise ValueError(f"{stage_id} requires the exact prior PR gate result")
        return (
            input_document,
            dict(input_document["gate"]),
            _reasons(input_document.get("reasons")),
        )

    @staticmethod
    def _result(
        context: StageExecutionContext,
        gate: dict[str, Any],
        reasons: list[str],
        additions: Mapping[str, Any],
    ) -> StageExecutionResult:
        artifact_type, _ = _STAGES[context.stage_id]
        return StageExecutionResult(
            artifact_kind=artifact_type,
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": artifact_type,
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "gate": gate,
                "reasons": reasons,
                **dict(additions),
            },
        )


__all__ = ["PrGateStageUseCase"]
