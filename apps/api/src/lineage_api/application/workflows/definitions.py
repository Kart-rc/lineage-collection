from __future__ import annotations

from dataclasses import dataclass

from lineage_api.application.models import WorkflowKind


TERMINAL_PREFIX = "terminal:"
RETRY_CLASSES = {"NONE", "BOUNDED_TRANSIENT"}
SIDE_EFFECT_MODES = {
    "PURE",
    "READ_ONLY",
    "IMMUTABLE_WRITE",
    "IDEMPOTENT_WRITE",
    "FENCED_WRITE",
    "CHECK_AND_AUDIT_WRITE",
}


@dataclass(frozen=True, slots=True)
class StageDefinition:
    stage_id: str
    name: str
    input_contracts: tuple[str, ...]
    output_contracts: tuple[str, ...]
    timeout_seconds: int
    max_attempts: int
    retry_class: str
    side_effect_mode: str
    idempotency_determinants: tuple[str, ...]
    on_success: tuple[str, ...]
    on_error: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    kind: WorkflowKind
    version: str
    entry_stage: str
    workflow_timeout_seconds: int
    stages: tuple[StageDefinition, ...]
    terminal_states: tuple[str, ...]

    @property
    def stage_ids(self) -> tuple[str, ...]:
        return tuple(stage.stage_id for stage in self.stages)

    def reachable_stage_ids(self) -> set[str]:
        by_id = {stage.stage_id: stage for stage in self.stages}
        pending = [self.entry_stage]
        reached: set[str] = set()
        while pending:
            stage_id = pending.pop()
            if stage_id in reached or stage_id not in by_id:
                continue
            reached.add(stage_id)
            stage = by_id[stage_id]
            pending.extend(
                target
                for target in stage.on_success + stage.on_error
                if not target.startswith(TERMINAL_PREFIX)
            )
        return reached

    def route_reaches_terminal(self, stage_id: str, route: str) -> bool:
        by_id = {stage.stage_id: stage for stage in self.stages}
        stage = by_id.get(stage_id)
        if stage is None or route not in {"success", "error"}:
            return False
        pending = list(stage.on_success if route == "success" else stage.on_error)
        visited: set[str] = set()
        while pending:
            target = pending.pop()
            if target.startswith(TERMINAL_PREFIX):
                return target.removeprefix(TERMINAL_PREFIX) in self.terminal_states
            if target in visited or target not in by_id:
                continue
            visited.add(target)
            next_stage = by_id[target]
            pending.extend(next_stage.on_success + next_stage.on_error)
        return False

    def validation_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        stage_ids = self.stage_ids
        known = set(stage_ids)
        if len(stage_ids) != len(known):
            errors.append("stage identifiers must be unique")
        if self.entry_stage not in known:
            errors.append(f"entry stage does not exist: {self.entry_stage}")
        if self.workflow_timeout_seconds < 1:
            errors.append("workflow timeout must be positive")
        if sum(stage.timeout_seconds for stage in self.stages) > self.workflow_timeout_seconds:
            errors.append("sequential critical path exceeds workflow timeout")
        if not self.terminal_states:
            errors.append("workflow has no terminal states")

        for stage in self.stages:
            if not 0 < stage.timeout_seconds <= self.workflow_timeout_seconds:
                errors.append(
                    f"{stage.stage_id} timeout must be positive and workflow-bounded"
                )
            if not 1 <= stage.max_attempts <= 5:
                errors.append(f"{stage.stage_id} attempts must be between 1 and 5")
            if not stage.input_contracts:
                errors.append(f"{stage.stage_id} has no input contract")
            if not stage.output_contracts:
                errors.append(f"{stage.stage_id} has no output contract")
            if not stage.idempotency_determinants:
                errors.append(f"{stage.stage_id} has no idempotency determinants")
            if stage.retry_class not in RETRY_CLASSES:
                errors.append(f"{stage.stage_id} has unknown retry class")
            if stage.side_effect_mode not in SIDE_EFFECT_MODES:
                errors.append(f"{stage.stage_id} has unknown side-effect mode")
            if not stage.on_success:
                errors.append(f"{stage.stage_id} has no success route")
            if not stage.on_error:
                errors.append(f"{stage.stage_id} has no error route")
            for target in stage.on_success + stage.on_error:
                if target.startswith(TERMINAL_PREFIX):
                    terminal = target.removeprefix(TERMINAL_PREFIX)
                    if terminal not in self.terminal_states:
                        errors.append(
                            f"{stage.stage_id} routes to undeclared terminal: {terminal}"
                        )
                elif target not in known:
                    errors.append(f"{stage.stage_id} routes to unknown stage: {target}")
            if stage.on_success and not self.route_reaches_terminal(stage.stage_id, "success"):
                errors.append(f"{stage.stage_id} success route has no terminal")
            if stage.on_error and not self.route_reaches_terminal(stage.stage_id, "error"):
                errors.append(f"{stage.stage_id} error route has no terminal")

        reached = self.reachable_stage_ids()
        unreachable = [stage_id for stage_id in stage_ids if stage_id not in reached]
        if unreachable:
            errors.append(f"unreachable stages: {','.join(unreachable)}")
        return tuple(errors)


def _linear_workflow(
    *,
    kind: WorkflowKind,
    prefix: str,
    names: tuple[str, ...],
    workflow_timeout_seconds: int,
    stage_timeouts: tuple[int, ...],
    side_effects: tuple[str, ...],
    terminal_states: tuple[str, ...],
    success_terminal: str,
    error_terminal: str,
) -> WorkflowDefinition:
    stages: list[StageDefinition] = []
    for index, (name, timeout, side_effect) in enumerate(
        zip(names, stage_timeouts, side_effects, strict=True), start=1
    ):
        stage_id = f"{prefix}{index}"
        next_target = (
            f"{prefix}{index + 1}"
            if index < len(names)
            else f"{TERMINAL_PREFIX}{success_terminal}"
        )
        previous_contract = (
            "durable-command/1.0.0"
            if index == 1
            else f"{kind.lower()}-{prefix.lower()}{index - 1}-result/1.0.0"
        )
        stages.append(
            StageDefinition(
                stage_id=stage_id,
                name=name,
                input_contracts=(previous_contract,),
                output_contracts=(
                    f"{kind.lower()}-{stage_id.lower()}-result/1.0.0",
                ),
                timeout_seconds=timeout,
                max_attempts=1 if side_effect == "PURE" else 3,
                retry_class="NONE" if side_effect == "PURE" else "BOUNDED_TRANSIENT",
                side_effect_mode=side_effect,
                idempotency_determinants=(
                    "workflowVersion",
                    "artifactDigest",
                    "determinantDigest",
                    "scope",
                    "stageId",
                ),
                on_success=(next_target,),
                on_error=(f"{TERMINAL_PREFIX}{error_terminal}",),
            )
        )
    return WorkflowDefinition(
        kind=kind,
        version="1.0.0",
        entry_stage=f"{prefix}1",
        workflow_timeout_seconds=workflow_timeout_seconds,
        stages=tuple(stages),
        terminal_states=terminal_states,
    )


BASELINE = _linear_workflow(
    kind="BASELINE",
    prefix="B",
    names=(
        "ACCEPT_AND_DEDUPLICATE_INTENT",
        "PIN_REPOSITORY_AND_DETERMINANTS",
        "CLASSIFY_REPOSITORY_AND_PATHS",
        "BUILD_COVERAGE_PLAN",
        "RUN_BOUNDED_STATIC_ANALYSIS",
        "VALIDATE_OPTIONAL_RUNTIME_EVIDENCE",
        "ANALYZE_RESIDUE_WITH_POLICY",
        "CONSOLIDATE_AND_VERIFY_COVERAGE",
        "CREATE_PROPOSAL_OR_AUTOPUBLISH_DECISION",
        "STAGE_VERIFY_FENCE_AND_ACTIVATE",
    ),
    workflow_timeout_seconds=43_200,
    stage_timeouts=(30, 120, 300, 120, 7_200, 900, 3_600, 1_800, 300, 600),
    side_effects=(
        "IDEMPOTENT_WRITE",
        "IDEMPOTENT_WRITE",
        "IDEMPOTENT_WRITE",
        "IDEMPOTENT_WRITE",
        "IMMUTABLE_WRITE",
        "IMMUTABLE_WRITE",
        "IMMUTABLE_WRITE",
        "IDEMPOTENT_WRITE",
        "IDEMPOTENT_WRITE",
        "FENCED_WRITE",
    ),
    terminal_states=(
        "PUBLISHED",
        "NO_LINEAGE",
        "REJECTED",
        "QUARANTINED",
        "FAILED_REDRIVABLE",
        "FAILED_TERMINAL",
    ),
    success_terminal="PUBLISHED",
    error_terminal="FAILED_REDRIVABLE",
)


INCREMENTAL = _linear_workflow(
    kind="INCREMENTAL",
    prefix="I",
    names=(
        "DEDUPLICATE_AND_PIN_ACTIVE_BASE",
        "COMPUTE_CHANGED_PATHS_AND_CLOSURE",
        "BUILD_DIFFERENTIAL_COVERAGE_PLAN",
        "FETCH_REQUIRED_IMMUTABLE_ARTIFACTS",
        "PROVE_NO_IMPACT_OR_EXTRACT_EVIDENCE",
        "VALIDATE_OPTIONAL_RUNTIME_EVIDENCE",
        "CONSOLIDATE_DELTAS_AND_TOMBSTONES",
        "RECHECK_BASE_AND_COVERAGE",
        "CREATE_DELTA_PROPOSAL",
        "PUBLISH_WITH_FENCED_PROTOCOL",
    ),
    workflow_timeout_seconds=1_800,
    stage_timeouts=(30, 120, 60, 180, 600, 180, 300, 60, 120, 150),
    side_effects=(
        "IDEMPOTENT_WRITE",
        "PURE",
        "IDEMPOTENT_WRITE",
        "READ_ONLY",
        "IMMUTABLE_WRITE",
        "IMMUTABLE_WRITE",
        "IDEMPOTENT_WRITE",
        "READ_ONLY",
        "IDEMPOTENT_WRITE",
        "FENCED_WRITE",
    ),
    terminal_states=(
        "PUBLISHED",
        "NO_LINEAGE_IMPACT",
        "REJECTED",
        "QUARANTINED",
        "FAILED_REDRIVABLE",
        "FAILED_TERMINAL",
    ),
    success_terminal="PUBLISHED",
    error_terminal="FAILED_REDRIVABLE",
)


PR_GATE = _linear_workflow(
    kind="PR_GATE",
    prefix="P",
    names=(
        "PIN_SIGNED_PR_HEAD_AND_POLICY",
        "FETCH_OR_BUILD_EXACT_CANDIDATE",
        "PIN_DEPLOYED_ARTIFACT_AND_GRAPH",
        "ANALYZE_RELEVANT_CHANGED_PATHS",
        "RUN_BOUNDED_IMPACT_TRAVERSAL",
        "EVALUATE_VERSIONED_GATE_POLICY",
        "RECHECK_HEAD_AND_ENVIRONMENT_POINTER",
        "UPSERT_STABLE_GITHUB_CHECK",
    ),
    workflow_timeout_seconds=120,
    stage_timeouts=(5, 30, 5, 35, 15, 10, 5, 5),
    side_effects=(
        "READ_ONLY",
        "READ_ONLY",
        "READ_ONLY",
        "PURE",
        "READ_ONLY",
        "PURE",
        "READ_ONLY",
        "CHECK_AND_AUDIT_WRITE",
    ),
    terminal_states=("PASS", "WARN", "BLOCK"),
    success_terminal="PASS",
    error_terminal="WARN",
)


DEPLOYMENT = _linear_workflow(
    kind="DEPLOYMENT",
    prefix="D",
    names=(
        "AUTHENTICATE_AND_DEDUPLICATE_OUTCOME",
        "ESTABLISH_AUTHORITATIVE_ORDERING",
        "RECORD_ACTUAL_DEPLOYED_DIGEST",
        "RESOLVE_EXACT_APPROVED_LINEAGE_PACKAGE",
        "RESERVE_FENCE_AND_PROMOTE",
        "READ_BACK_AND_VERIFY_CORRELATION",
    ),
    workflow_timeout_seconds=600,
    stage_timeouts=(30, 30, 60, 120, 240, 120),
    side_effects=(
        "IDEMPOTENT_WRITE",
        "IDEMPOTENT_WRITE",
        "IDEMPOTENT_WRITE",
        "READ_ONLY",
        "FENCED_WRITE",
        "READ_ONLY",
    ),
    terminal_states=(
        "PROMOTED",
        "FAILED_NO_CHANGE",
        "ROLLED_BACK",
        "LINEAGE_OUT_OF_SYNC",
        "FAILED_TERMINAL",
    ),
    success_terminal="PROMOTED",
    error_terminal="FAILED_TERMINAL",
)


NIGHTLY = _linear_workflow(
    kind="NIGHTLY",
    prefix="N",
    names=(
        "RECONCILE_EVENTS_RECEIPTS_AND_ARCHIVES",
        "SAMPLE_PUBLISHED_LINEAGE_AGAINST_CLEAN_ANALYSIS",
        "VERIFY_PROJECTION_CHECKSUMS",
        "DRAIN_BOUNDED_STALE_LLM_CACHE",
        "EVALUATE_AUTOPUBLISH_AUDIT_SAMPLE",
        "EMIT_PROPOSALS_ALERTS_AND_EVIDENCE",
    ),
    workflow_timeout_seconds=14_400,
    stage_timeouts=(1_800, 7_200, 1_800, 1_800, 900, 300),
    side_effects=(
        "IDEMPOTENT_WRITE",
        "IMMUTABLE_WRITE",
        "READ_ONLY",
        "IDEMPOTENT_WRITE",
        "READ_ONLY",
        "IDEMPOTENT_WRITE",
    ),
    terminal_states=(
        "RECONCILED",
        "PROPOSALS_RAISED",
        "FAILED_REDRIVABLE",
        "FAILED_TERMINAL",
    ),
    success_terminal="RECONCILED",
    error_terminal="FAILED_REDRIVABLE",
)


WORKFLOWS: dict[WorkflowKind, WorkflowDefinition] = {
    workflow.kind: workflow
    for workflow in (BASELINE, INCREMENTAL, PR_GATE, DEPLOYMENT, NIGHTLY)
}
