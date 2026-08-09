from __future__ import annotations

from typing import Any, Mapping

from lineage_api.application.classification import (
    ClassificationEvidence,
    evaluate_classification,
)
from lineage_api.application.stage_execution import (
    StageExecutionContext,
    StageExecutionResult,
    StageUseCase,
)


_CONTEXT_FIELDS = {
    "artifactDigest": ("artifactDigest", "digest"),
    "environment": ("environment", "env"),
    "repository": ("repository", "repo", "repoOrPath"),
    "system": ("system",),
}
_EVIDENCE_KEYS = frozenset({"level", "source", "class", "repositoryClass", "ref"})


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 2_048:
        raise ValueError(f"invalid {name}")
    return value


def _bounded_context(document: Mapping[str, Any]) -> dict[str, str]:
    inherited = document.get("context")
    sources = [inherited, document] if isinstance(inherited, Mapping) else [document]
    context: dict[str, str] = {}
    for output_name, aliases in _CONTEXT_FIELDS.items():
        for source in sources:
            value = next((source.get(alias) for alias in aliases if alias in source), None)
            if value is not None:
                context[output_name] = _required_text(output_name, value)
                break
    for required in ("repository", "artifactDigest", "environment", "system"):
        if required not in context:
            raise ValueError(f"classification input is missing {required}")
    return context


class ClassificationStageUseCase:
    def __init__(self, *, policy_version: str) -> None:
        self._policy_version = _required_text("classification policy version", policy_version)

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if not isinstance(input_document, Mapping):
            raise ValueError("classification input must be an object")
        if input_document.get("schemaVersion") != "1.0.0":
            raise ValueError("unsupported classification input schema")
        lineage_context = _bounded_context(input_document)
        raw_evidence = input_document.get("classificationEvidence", input_document.get("evidence"))
        if not isinstance(raw_evidence, list):
            raise ValueError("classification evidence must be an array")
        evidence: list[ClassificationEvidence] = []
        for item in raw_evidence:
            if not isinstance(item, Mapping) or set(item) - _EVIDENCE_KEYS:
                raise ValueError("invalid classification evidence item")
            if (
                "class" in item
                and "repositoryClass" in item
                and item["class"] != item["repositoryClass"]
            ):
                raise ValueError("classification evidence class aliases conflict")
            repository_class = item.get("repositoryClass", item.get("class"))
            evidence.append(
                ClassificationEvidence(
                    level=item.get("level"),  # type: ignore[arg-type]
                    source=item.get("source"),  # type: ignore[arg-type]
                    repository_class=repository_class,  # type: ignore[arg-type]
                    ref=item.get("ref"),  # type: ignore[arg-type]
                )
            )
        decision = evaluate_classification(
            lineage_context["repository"],
            evidence,
            self._policy_version,
            context.correlation_id,
        )
        decision_document = {
            "decisionId": decision.decision_id,
            "repoOrPath": decision.repo_or_path,
            "repositoryClass": decision.repository_class,
            "baselineTreatment": decision.baseline_treatment,
            "onChangeTreatment": decision.on_change_treatment,
            "evidenceLevelUsed": decision.evidence_level_used,
            "evidenceRefs": list(decision.evidence_refs),
            "policyVersion": decision.policy_version,
            "status": decision.status,
            "reason": decision.reason,
        }
        return StageExecutionResult(
            artifact_kind="classification-decision",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "classification-decision",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": lineage_context,
                "decision": decision_document,
            },
        )


def production_stage_use_cases() -> dict[tuple[str, str], StageUseCase]:
    return {("BASELINE", "B3"): ClassificationStageUseCase(policy_version="1.0.0")}


__all__ = ["ClassificationStageUseCase", "production_stage_use_cases"]
