from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Callable

from lineage_api.application.models import Command, LaneMessage, Lease, StageIdentity, StageResult
from lineage_api.application.outbox import OutboxDispatcher
from lineage_api.application.ports import ClockPort, CommandStorePort, LaneBrokerPort
from lineage_api.application.workflows.baseline import BaselineWorkflow
from lineage_api.application.workflows.deployment import DeploymentPackage, DeploymentStorePort
from lineage_api.application.workflows.incremental import IncrementalWorkflow
from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.domain.evidence import EvidenceRef
from lineage_api.services.classification import (
    ClassificationEvidence,
    ClassificationService,
)
from lineage_api.services.analyzer_registry import (
    AnalyzerRegistry,
    AnalyzerSelection,
    AnalyzerSnapshotProvider,
)
from lineage_api.services.consolidation import ConsolidationService, MechanismAssertion
from lineage_api.services.evidence_store import EvidenceStore
from lineage_api.services.intake import IntakeService, PushDelivery
from lineage_api.services.publisher import PublisherService
from lineage_api.services.resolver import Resolver
from lineage_api.services.review import ReviewService
from lineage_api.services.runtime import RuntimeLineageService


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _combined_verdict(verdicts: list[str]) -> str | None:
    real = [v for v in verdicts if v != "NOT_PROVIDED"]
    if not real:
        return None
    if all(v == "CORROBORATED" for v in real):
        return "CORROBORATED"
    return "PARTIALLY_CORROBORATED"


class TargetCommandProcessingError(RuntimeError):
    """A bounded failure to drive one durable command to a terminal state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message[:160])
        self.code = code


class OrchestrationService:
    def __init__(
        self,
        database: Database,
        intake: IntakeService,
        classification: ClassificationService,
        analyzer_registry: AnalyzerRegistry,
        snapshot_provider: AnalyzerSnapshotProvider,
        store: EvidenceStore,
        consolidation: ConsolidationService,
        review: ReviewService,
        publisher: PublisherService,
        runtime: RuntimeLineageService,
        deployment_store: DeploymentStorePort,
        command_store: CommandStorePort,
        outbox_dispatcher: OutboxDispatcher,
        broker: LaneBrokerPort,
        durable_clock: ClockPort,
        clock: Callable[[], str] = _utc_now,
        resolver: Resolver | None = None,
    ) -> None:
        self._database = database
        self._intake = intake
        self._classification = classification
        self._analyzer_registry = analyzer_registry
        self._snapshot_provider = snapshot_provider
        self._store = store
        self._consolidation = consolidation
        self._review = review
        self._publisher = publisher
        self._runtime = runtime
        self._deployment_store = deployment_store
        self._command_store = command_store
        self._outbox_dispatcher = outbox_dispatcher
        self._broker = broker
        self._durable_clock = durable_clock
        self._clock = clock
        self._resolver = resolver
        self._fault_injector: Callable[[str], None] | None = None

    def set_fault_injector(self, injector: Callable[[str], None] | None) -> None:
        self._fault_injector = injector

    def process_push(
        self,
        delivery: PushDelivery,
        classification_evidence: list[ClassificationEvidence] | None = None,
    ) -> dict[str, Any]:
        intake = self._intake.accept(delivery)
        if intake.outcome == "QUARANTINED":
            return {
                "outcome": intake.outcome,
                "reason": intake.reason,
                "quarantineId": intake.quarantine_id,
                "eventId": intake.event_id,
                "run": None,
                "proposal": None,
                "command": None,
            }
        command = intake.command
        if command is None:
            raise RuntimeError("durable intake returned no command")
        result = self.process_command(
            command.command_id,
            classification_evidence=classification_evidence,
        )
        if intake.outcome == "DUPLICATE" and result["outcome"] in {
            "ACCEPTED",
            "NO_LINEAGE",
            "REUSED",
        }:
            result.update(outcome="DUPLICATE", reason="DUPLICATE")
        return result

    def process_command(
        self,
        command_id: str,
        *,
        classification_evidence: list[ClassificationEvidence] | None = None,
        max_messages: int = 100,
    ) -> dict[str, Any]:
        if not command_id or len(command_id) > 128:
            raise ValueError("target command id must be bounded")
        if max_messages < 1 or max_messages > 10_000:
            raise ValueError("target processing bound must be between 1 and 10000")
        for _ in range(max_messages):
            current = self._command_store.get(command_id)
            if current is None:
                raise TargetCommandProcessingError(
                    "TARGET_COMMAND_NOT_FOUND", "target command does not exist"
                )
            if current.status == "COMPLETED":
                return self._result_for_completed_command(current)
            if current.status == "FAILED_TERMINAL":
                raise TargetCommandProcessingError(
                    "TARGET_COMMAND_FAILED", "target command reached terminal failure"
                )
            try:
                processed = self.worker_once(
                    classification_evidence=classification_evidence,
                    classification_command_id=command_id,
                )
            except Exception:
                after_failure = self._command_store.get(command_id)
                if after_failure is not None and (
                    after_failure.status != current.status
                    or after_failure.attempt != current.attempt
                ):
                    raise
                continue
            if processed is None:
                terminal = self._command_store.get(command_id)
                if terminal is not None and terminal.status == "COMPLETED":
                    return self._result_for_completed_command(terminal)
                raise TargetCommandProcessingError(
                    "TARGET_COMMAND_STALLED",
                    "target command did not reach a terminal state",
                )
            processed_command = processed.get("command")
            if (
                isinstance(processed_command, dict)
                and processed_command.get("commandId") == command_id
            ):
                return processed
        raise TargetCommandProcessingError(
            "TARGET_COMMAND_BOUND_EXCEEDED",
            "target command did not reach a terminal state within the processing bound",
        )

    def worker_once(
        self,
        classification_evidence: list[ClassificationEvidence] | None = None,
        classification_command_id: str | None = None,
    ) -> dict[str, Any] | None:
        self._outbox_dispatcher.dispatch(limit=100)
        for lane in ("interactive", "events", "bulk"):
            message = self._broker.claim(
                lane,
                "local-worker",
                visibility_timeout_seconds=60,
            )
            if message is not None:
                evidence = (
                    classification_evidence
                    if classification_command_id is None
                    or message.payload_ref == f"command://{classification_command_id}"
                    else None
                )
                return self._handle_message(message, evidence)
        return None

    def worker_drain(self, max_messages: int = 100) -> list[dict[str, Any]]:
        if max_messages < 1:
            raise ValueError("max messages must be positive")
        processed: list[dict[str, Any]] = []
        while len(processed) < max_messages:
            result = self.worker_once()
            if result is None:
                break
            processed.append(result)
        return processed

    def _handle_message(
        self,
        message: LaneMessage,
        classification_evidence: list[ClassificationEvidence] | None,
    ) -> dict[str, Any]:
        if not message.payload_ref.startswith("command://"):
            self._broker.retry(
                message,
                available_at=self._durable_clock.now(),
                error_code="INVALID_COMMAND_REFERENCE",
            )
            raise RuntimeError(f"invalid command reference {message.payload_ref}")
        command_id = message.payload_ref.removeprefix("command://")
        command = self._command_store.get(command_id)
        if command is None:
            self._broker.retry(
                message,
                available_at=self._durable_clock.now(),
                error_code="COMMAND_NOT_FOUND",
            )
            raise RuntimeError(f"command {command_id} does not exist")
        if command.status == "COMPLETED":
            self._broker.acknowledge(message)
            return self._result_for_completed_command(command)

        lease = self._command_store.claim(command_id, "local-worker", lease_seconds=60)
        completed: Command | None = None
        try:
            envelope = self._event_envelope(command)
            result = self._process_accepted(
                command,
                lease,
                envelope,
                classification_evidence,
            )
            output_ref = self._canonical(
                {
                    "schemaVersion": "1.0.0",
                    "outcome": result["outcome"],
                    "reason": result.get("reason"),
                }
            )
            body = output_ref.encode()
            completed = self._command_store.complete(
                lease,
                StageResult(
                    identity=StageIdentity(
                        workflow_kind=command.workflow_kind,
                        scope=command.scope,
                        artifact_digest=command.artifact_digest,
                        stage_name="WORKFLOW",
                        determinant_digest=command.determinant_digest,
                        schema_version=command.workflow_version,
                    ),
                    command_id=command.command_id,
                    output_ref=output_ref,
                    output_checksum=f"sha256:{hashlib.sha256(body).hexdigest()}",
                    lease_epoch=lease.epoch,
                    completed_at=self._durable_clock.now(),
                ),
            )
            if self._fault_injector is not None:
                self._fault_injector("COMMAND_COMPLETED")
        except Exception:
            current = self._command_store.get(command_id)
            if current is not None and current.status != "COMPLETED":
                self._command_store.fail(lease, "WORKFLOW_EXECUTION_FAILED", retryable=True)
            self._broker.retry(
                message,
                available_at=self._durable_clock.now(),
                error_code="WORKFLOW_EXECUTION_FAILED",
            )
            raise

        self._broker.acknowledge(message)
        if completed is None:  # pragma: no cover - defensive invariant
            raise RuntimeError(f"command {command_id} completion was not recorded")
        result["command"] = self._command_dict(completed)
        return result

    def _process_accepted(
        self,
        command: Command,
        lease: Lease,
        envelope: dict[str, Any],
        classification_evidence: list[ClassificationEvidence] | None = None,
    ) -> dict[str, Any]:
        if command.workflow_kind == "BASELINE":
            return self._run_baseline(
                command,
                lease,
                envelope,
                classification_evidence,
            )
        if command.workflow_kind == "INCREMENTAL":
            return self._run_incremental(
                command,
                lease,
                envelope,
                classification_evidence,
            )
        run_id = f"run-{hashlib.sha256(envelope['eventId'].encode()).hexdigest()[:20]}"
        correlation_id = envelope["correlationId"]
        now = self._clock()
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, event_id, repo, digest, env, system, state,
                    correlation_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?)
                """,
                (
                    run_id,
                    envelope["eventId"],
                    envelope["repo"],
                    envelope["digest"],
                    envelope["env"],
                    envelope["system"],
                    correlation_id,
                    now,
                    now,
                ),
            )
        self._stage(run_id, "QUEUED", {"lane": envelope["lane"]})

        evidence = (
            self._analyzer_registry.classification_evidence(
                self._snapshot_provider.resolve(envelope),
                AnalyzerSelection.from_envelope(envelope),
            )
            if classification_evidence is None
            else classification_evidence
        )
        decision = self._classification.classify(
            envelope["repo"], evidence, correlation_id
        )
        self._stage(
            run_id,
            "CLASSIFYING",
            {
                "decisionId": decision.decision_id,
                "repositoryClass": decision.repository_class,
                "evidenceLevel": decision.evidence_level_used,
            },
        )
        if decision.repository_class == "UNKNOWN":
            self._fail(run_id, "CLASSIFYING", "UNKNOWN_CLASSIFICATION")
            return {
                "outcome": "BLOCKED",
                "reason": "UNKNOWN_CLASSIFICATION",
                "eventId": envelope["eventId"],
                "run": self.get_run(run_id),
                "proposal": None,
            }

        analysis = self._analyze(envelope, run_id)
        sca = analysis.document
        self._stage(
            run_id,
            "ANALYZING",
            {"filesAnalyzed": sca["stats"]["filesAnalyzed"], "edgesEmitted": len(sca["edges"])},
        )
        self._stage(
            run_id,
            "RESOLVING",
            {"datasetsSeen": list(sca["datasetsSeen"]), "residueCount": len(sca["residue"])},
        )

        sca_ref = self._store.put(
            "sca", f"{envelope['system']}/{run_id}", sca, str(sca["schemaVersion"])
        )
        runtime_body = self._runtime_fixture_document(sca)
        runtime_ref = self._store.put(
            "runtime",
            f"{envelope['system']}/{run_id}",
            runtime_body,
            "1.0.0",
        )
        self._stage(
            run_id,
            "STORING_EVIDENCE",
            {
                "scaEvidenceRef": sca_ref.as_dict(),
                "runtimeEvidenceRef": runtime_ref.as_dict(),
            },
        )

        merged = []
        for sca_edge in sca["edges"]:
            static_assertion = MechanismAssertion(
                provenance_id=sca_edge["provenanceId"],
                from_urns=tuple(sca_edge["from"]),
                to_urn=sca_edge["to"],
                edge_type=sca_edge["edgeType"],
                transform=sca_edge.get("transform"),
                mechanism="SCA",
                exact=bool(sca_edge["exact"]),
                evidence_ref=sca_ref.as_dict(),
                repo=sca_edge["repo"],
                run_id=run_id,
                correlation_id=correlation_id,
                citation=sca_edge.get("evidence"),
            )
            runtime_assertion = MechanismAssertion(
                provenance_id=f"runtime-{sca_edge['provenanceId']}",
                from_urns=tuple(sca_edge["from"]),
                to_urn=sca_edge["to"],
                edge_type=sca_edge["edgeType"],
                transform=None,
                mechanism="RUNTIME",
                exact=True,
                evidence_ref=runtime_ref.as_dict(),
                repo=sca_edge["repo"],
                run_id=run_id,
                correlation_id=correlation_id,
                runtime_scope="ELEMENT",
                session_complete=True,
            )
            merged.append(
                self._consolidation.merge_many([static_assertion, runtime_assertion])
            )
        edge_payloads = [edge.as_dict() for edge in merged]
        self._stage(
            run_id,
            "MERGING",
            {"edgeCount": len(edge_payloads), "bands": sorted({edge.band for edge in merged})},
        )

        pointer = self._publisher.pointer(envelope["env"])
        proposal = self._review.create(
            edge_payloads, pointer.active_version, correlation_id
        )
        self._stage(
            run_id,
            "PROPOSING",
            {"proposalId": proposal.proposal_id, "proposalVersion": proposal.version},
        )
        self._stage(run_id, "IN_REVIEW", {"proposalId": proposal.proposal_id})
        return {
            "outcome": "ACCEPTED",
            "reason": None,
            "eventId": envelope["eventId"],
            "run": self.get_run(run_id),
            "proposal": proposal.as_dict(),
        }

    def _run_incremental(
        self,
        command: Command,
        lease: Lease,
        envelope: dict[str, Any],
        classification_evidence: list[ClassificationEvidence] | None,
    ) -> dict[str, Any]:
        workflow = IncrementalWorkflow(
            command,
            lease,
            self._command_store,
            self._store,
            self._durable_clock,
            self._fault_injector,
        )
        i1 = workflow.checkpoint("I1", lambda: self._incremental_start(envelope))
        i2 = workflow.checkpoint("I2", lambda: self._incremental_changed_scope(envelope))
        i3 = workflow.checkpoint(
            "I3", lambda: self._incremental_coverage_plan(command, i2)
        )
        i4 = workflow.checkpoint(
            "I4",
            lambda: self._incremental_classify(
                envelope,
                i1["runId"],
                classification_evidence,
            ),
        )
        if i4["repositoryClass"] == "UNKNOWN":
            self._fail(i1["runId"], "CLASSIFYING", "UNKNOWN_CLASSIFICATION")
            return {
                "outcome": "BLOCKED",
                "reason": "UNKNOWN_CLASSIFICATION",
                "eventId": envelope["eventId"],
                "run": self.get_run(i1["runId"]),
                "proposal": None,
                "resume": {"reusedStages": workflow.reused_stage_ids},
            }

        i5 = workflow.checkpoint(
            "I5", lambda: self._incremental_sca(envelope, i1["runId"], i3)
        )
        i6 = workflow.checkpoint(
            "I6", lambda: self._incremental_runtime(envelope, i1["runId"], i5)
        )
        if i5["analysis"]["status"] != "COMPLETE":
            i8 = workflow.checkpoint(
                "I8", lambda: self._incremental_recheck(command, i1, i3, i6)
            )
            self._fail(i1["runId"], "ANALYZING", "INTEGRATION_REQUIRED")
            result = {
                "outcome": "INTEGRATION_REQUIRED",
                "reason": "INTEGRATION_REQUIRED",
                "eventId": envelope["eventId"],
                "run": self.get_run(i1["runId"]),
                "proposal": None,
                "coverageManifest": i8["coverageManifest"],
                "analysis": i5["analysis"],
                "runtimeStatus": i6["runtime"].get("verification") or i6["runtime"]["status"],
                "resume": {"reusedStages": workflow.reused_stage_ids},
            }
            if envelope.get("runtimeExecution") is True:
                result["runtimeReasons"] = i6["runtime"].get("reasons", [])
            return result
        i7 = workflow.checkpoint(
            "I7", lambda: self._incremental_consolidate(i1["runId"], i5, i6)
        )
        i8 = workflow.checkpoint(
            "I8", lambda: self._incremental_recheck(command, i1, i3, i6)
        )
        i9 = workflow.checkpoint(
            "I9", lambda: self._incremental_propose(i1["runId"], i1, i7)
        )
        i10 = workflow.checkpoint(
            "I10",
            lambda: self._incremental_finalize(command, i5, i6, i7, i8, i9),
        )
        result = {
            "outcome": "ACCEPTED",
            "reason": None,
            "eventId": envelope["eventId"],
            "run": self.get_run(i1["runId"]),
            "proposal": i9["proposal"],
            "coverageManifest": i8["coverageManifest"],
            "evidenceManifest": i10["evidenceManifest"],
            "analysis": i5["analysis"],
            "runtimeStatus": i6["runtime"].get("verification") or i6["runtime"]["status"],
            "resume": {"reusedStages": workflow.reused_stage_ids},
        }
        if envelope.get("runtimeExecution") is True:
            result["runtimeReasons"] = i6["runtime"].get("reasons", [])
        return result

    def _run_baseline(
        self,
        command: Command,
        lease: Lease,
        envelope: dict[str, Any],
        classification_evidence: list[ClassificationEvidence] | None,
    ) -> dict[str, Any]:
        workflow = BaselineWorkflow(
            command,
            lease,
            self._command_store,
            self._store,
            self._durable_clock,
            self._fault_injector,
        )
        b1 = workflow.checkpoint("B1", lambda: self._incremental_start(envelope))
        b2 = workflow.checkpoint("B2", lambda: self._baseline_pins(envelope))
        b3 = workflow.checkpoint(
            "B3",
            lambda: self._incremental_classify(
                envelope,
                b1["runId"],
                classification_evidence,
            ),
        )
        if b3["repositoryClass"] == "UNKNOWN":
            self._fail(b1["runId"], "CLASSIFYING", "UNKNOWN_CLASSIFICATION")
            return {
                "outcome": "BLOCKED",
                "reason": "UNKNOWN_CLASSIFICATION",
                "eventId": envelope["eventId"],
                "run": self.get_run(b1["runId"]),
                "proposal": None,
                "resume": {"reusedStages": workflow.reused_stage_ids},
            }

        b4 = workflow.checkpoint(
            "B4",
            lambda: self._analyzer_registry.baseline_plan(
                self._snapshot_provider.resolve(envelope),
                AnalyzerSelection.from_envelope(envelope),
                max_fanout=1_000,
            ),
        )
        b5 = workflow.checkpoint(
            "B5", lambda: self._incremental_sca(envelope, b1["runId"], b4)
        )
        b6 = workflow.checkpoint(
            "B6", lambda: self._incremental_runtime(envelope, b1["runId"], b5)
        )
        b7 = workflow.checkpoint("B7", lambda: self._baseline_residue(b5))
        b8 = workflow.checkpoint(
            "B8", lambda: self._baseline_consolidate(command, b1, b4, b5, b6)
        )
        if b8["coverageManifest"]["state"] != "COMPLETE":
            self._fail(b1["runId"], "COVERAGE", "INCOMPLETE_COVERAGE")
            return {
                "outcome": "INCOMPLETE",
                "reason": "INCOMPLETE_COVERAGE",
                "eventId": envelope["eventId"],
                "run": self.get_run(b1["runId"]),
                "proposal": None,
                "coverageManifest": b8["coverageManifest"],
                "resume": {"reusedStages": workflow.reused_stage_ids},
            }
        if b8["edges"]:
            b9 = workflow.checkpoint(
                "B9", lambda: self._incremental_propose(b1["runId"], b1, b8)
            )
        else:
            b9 = workflow.checkpoint(
                "B9", lambda: self._baseline_no_lineage(b1["runId"])
            )
        b10 = workflow.checkpoint(
            "B10",
            lambda: self._baseline_finalize(command, b5, b6, b7, b8, b9, b2),
        )
        no_lineage = b9.get("decision") == "NO_LINEAGE"
        result = {
            "outcome": "NO_LINEAGE" if no_lineage else "ACCEPTED",
            "reason": "NO_LINEAGE_EVIDENCE" if no_lineage else None,
            "eventId": envelope["eventId"],
            "run": self.get_run(b1["runId"]),
            "proposal": b9["proposal"],
            "coverageManifest": b8["coverageManifest"],
            "evidenceManifest": b10["evidenceManifest"],
            "runtimeStatus": b6["runtime"].get("verification") or b6["runtime"]["status"],
            "resume": {"reusedStages": workflow.reused_stage_ids},
        }
        if envelope.get("runtimeExecution") is True:
            result["runtimeReasons"] = b6["runtime"].get("reasons", [])
        return result

    def _baseline_pins(self, envelope: dict[str, Any]) -> dict[str, Any]:
        selection = AnalyzerSelection.from_envelope(envelope)
        snapshot = self._snapshot_provider.resolve(envelope)
        analyzer_pins = self._analyzer_registry.pins(snapshot, selection)
        return {
            "artifactDigest": envelope["digest"],
            "environment": envelope["env"],
            **analyzer_pins,
            "classificationPolicyVersion": "1.0.0",
        }

    @staticmethod
    def _baseline_residue(sca: dict[str, Any]) -> dict[str, Any]:
        residue = sca["sca"]["residue"]
        return {
            "residue": {
                "status": "SKIPPED_WITH_RECORD",
                "count": len(residue),
                "reason": "LLM_NOT_CONFIGURED",
            }
        }

    def _baseline_consolidate(
        self,
        command: Command,
        start: dict[str, Any],
        plan: dict[str, Any],
        sca: dict[str, Any],
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        consolidation = self._incremental_consolidate(start["runId"], sca, runtime)
        state = self._coverage_state(plan)
        identity = {
            "artifactDigest": command.artifact_digest,
            "determinantDigest": command.determinant_digest,
            "expectedScope": plan["expectedScope"],
            "scopeDispositionDigest": plan["scopeDispositionDigest"],
            "runtimeEvidence": BaselineWorkflow.runtime_coverage(runtime),
            "scope": command.scope,
        }
        manifest_id = (
            f"coverage-{hashlib.sha256(self._canonical(identity).encode()).hexdigest()[:20]}"
        )
        manifest = {
            "schemaVersion": "1.0.0",
            "manifestId": manifest_id,
            "workflowKind": command.workflow_kind,
            "scope": command.scope,
            "artifactDigest": command.artifact_digest,
            "determinantDigest": command.determinant_digest,
            "sourceScopeDispositionDigest": plan["scopeDispositionDigest"],
            "state": state,
            "expectedScope": plan["expectedScope"],
            "completedScope": plan["recomputedScope"],
            "reusedScope": [],
            "skippedScope": plan["skippedScope"],
            "unsupportedScope": plan["unsupportedScope"],
            "quarantinedScope": [],
            "failedScope": plan["failedScope"],
            "runtimeEvidence": BaselineWorkflow.runtime_coverage(runtime),
        }
        payload = self._canonical(manifest)
        now = self._clock()
        with self._database.transaction() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM coverage_manifests WHERE manifest_id = ?",
                (manifest_id,),
            ).fetchone()
            if existing is not None and existing["payload_json"] != payload:
                raise RuntimeError(f"coverage manifest conflict: {manifest_id}")
            connection.execute(
                """
                INSERT OR IGNORE INTO coverage_manifests(
                    manifest_id, workflow_kind, scope, artifact_digest,
                    determinant_digest, state, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest_id,
                    command.workflow_kind,
                    command.scope,
                    command.artifact_digest,
                    command.determinant_digest,
                    state,
                    payload,
                    now,
                    now,
                ),
            )
        return {**consolidation, "coverageManifest": manifest}

    def _baseline_no_lineage(self, run_id: str) -> dict[str, Any]:
        self._stage(
            run_id,
            "NO_LINEAGE",
            {"reason": "NO_LINEAGE_EVIDENCE", "proposalCreated": False},
        )
        return {"decision": "NO_LINEAGE", "proposal": None}

    @staticmethod
    def _baseline_finalize(
        command: Command,
        sca: dict[str, Any],
        runtime: dict[str, Any],
        residue: dict[str, Any],
        consolidation: dict[str, Any],
        proposal: dict[str, Any],
        pins: dict[str, Any],
    ) -> dict[str, Any]:
        edge_summary = [
            {
                "edgeKey": edge["edgeKey"],
                "version": edge["version"],
                "band": edge["band"],
                "status": edge["status"],
            }
            for edge in sorted(consolidation["edges"], key=lambda item: item["edgeKey"])
        ]
        return {
            "evidenceManifest": {
                "schemaVersion": "1.0.0",
                "commandId": command.command_id,
                "workflowKind": command.workflow_kind,
                "artifactDigest": command.artifact_digest,
                "determinantDigest": command.determinant_digest,
                "pins": pins,
                "coverageManifestId": consolidation["coverageManifest"]["manifestId"],
                "sca": sca["scaRef"],
                "runtime": runtime["runtime"],
                "residue": residue["residue"],
                "edges": edge_summary,
                "proposal": (
                    {
                        "proposalId": proposal["proposal"]["proposalId"],
                        "version": proposal["proposal"]["version"],
                    }
                    if proposal["proposal"] is not None
                    else None
                ),
            }
        }

    def _incremental_start(self, envelope: dict[str, Any]) -> dict[str, Any]:
        run_id = f"run-{hashlib.sha256(envelope['eventId'].encode()).hexdigest()[:20]}"
        now = self._clock()
        pointer = self._publisher.pointer(envelope["env"])
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO runs(
                    run_id, event_id, repo, digest, env, system, state,
                    correlation_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?)
                """,
                (
                    run_id,
                    envelope["eventId"],
                    envelope["repo"],
                    envelope["digest"],
                    envelope["env"],
                    envelope["system"],
                    envelope["correlationId"],
                    now,
                    now,
                ),
            )
        self._stage(run_id, "QUEUED", {"lane": envelope["lane"]})
        return {
            "runId": run_id,
            "environment": envelope["env"],
            "activeBaseVersion": pointer.active_version,
            "activeBaseFence": pointer.fencing_token,
            "artifactDigest": envelope["digest"],
        }

    def _incremental_changed_scope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        changed = sorted(set(envelope["changedFiles"]))
        source_scope = self._analyzer_registry.source_scope(
            self._snapshot_provider.resolve(envelope),
            AnalyzerSelection.from_envelope(envelope),
        ).as_dict()
        if changed != source_scope["expectedScope"]:
            raise TargetCommandProcessingError(
                "SOURCE_SCOPE_MISMATCH",
                "changed paths do not match the immutable source scope",
            )
        return {
            "changedPaths": changed,
            "affectedScope": changed,
            "removedPaths": [],
            "sourceScope": source_scope,
        }

    @staticmethod
    def _incremental_coverage_plan(
        command: Command,
        changed: dict[str, Any],
    ) -> dict[str, Any]:
        source_scope = changed["sourceScope"]
        return {
            "workflowKind": command.workflow_kind,
            "artifactDigest": command.artifact_digest,
            "determinantDigest": command.determinant_digest,
            "scopeDispositionDigest": source_scope["scopeDispositionDigest"],
            "expectedScope": source_scope["expectedScope"],
            "selectedScope": source_scope["selectedScope"],
            "recomputedScope": source_scope["recomputedScope"],
            "reusedScope": [],
            "removedScope": changed["removedPaths"],
            "skippedScope": source_scope["skippedScope"],
            "unsupportedScope": source_scope["unsupportedScope"],
            "failedScope": source_scope["failedScope"],
        }

    def _incremental_classify(
        self,
        envelope: dict[str, Any],
        run_id: str,
        classification_evidence: list[ClassificationEvidence] | None,
    ) -> dict[str, Any]:
        evidence = (
            self._analyzer_registry.classification_evidence(
                self._snapshot_provider.resolve(envelope),
                AnalyzerSelection.from_envelope(envelope),
            )
            if classification_evidence is None
            else classification_evidence
        )
        decision = self._classification.classify(
            envelope["repo"], evidence, envelope["correlationId"]
        )
        detail = {
            "decisionId": decision.decision_id,
            "repositoryClass": decision.repository_class,
            "evidenceLevel": decision.evidence_level_used,
        }
        self._stage(run_id, "CLASSIFYING", detail)
        return detail

    def _incremental_sca(
        self,
        envelope: dict[str, Any],
        run_id: str,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        durable_scope = self._analyzer_registry.source_scope(
            self._snapshot_provider.resolve(envelope),
            AnalyzerSelection.from_envelope(envelope),
        ).as_dict()
        for key in (
            "scopeDispositionDigest",
            "expectedScope",
            "selectedScope",
            "recomputedScope",
            "skippedScope",
            "unsupportedScope",
            "failedScope",
        ):
            if plan[key] != durable_scope[key]:
                raise TargetCommandProcessingError(
                    "SOURCE_SCOPE_DISPOSITION_MISMATCH",
                    "durable source scope changed between workflow stages",
                )
        analysis = self._analyze(envelope, run_id)
        sca = analysis.document
        sca_ref = self._store.put(
            "sca", f"{envelope['system']}/{run_id}", sca, str(sca["schemaVersion"])
        )
        self._stage(
            run_id,
            "ANALYZING",
            {"filesAnalyzed": sca["stats"]["filesAnalyzed"], "edgesEmitted": len(sca["edges"])},
        )
        self._stage(
            run_id,
            "RESOLVING",
            {"datasetsSeen": list(sca["datasetsSeen"]), "residueCount": len(sca["residue"])},
        )
        return {
            "sca": sca,
            "scaRef": sca_ref.as_dict(),
            "analysis": {
                "status": analysis.status,
                "statusReasons": list(analysis.status_reasons),
                "edgeCount": analysis.edge_count,
                "readCount": analysis.read_count,
                "writeCount": analysis.write_count,
                "residueCount": analysis.residue_count,
                "unresolvedCount": analysis.unresolved_count,
                "scopeDispositionDigest": durable_scope["scopeDispositionDigest"],
            },
        }

    def _analyze(self, envelope: dict[str, Any], run_id: str):
        return self._analyzer_registry.analyze(
            self._snapshot_provider.resolve(envelope),
            AnalyzerSelection.from_envelope(envelope),
            run_id,
            str(envelope["correlationId"]),
        )

    def _incremental_runtime(
        self,
        envelope: dict[str, Any],
        run_id: str,
        sca: dict[str, Any],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        verification: str | None = None
        if envelope.get("runtimeExecution") is True:
            verification, reasons = self._execute_runtime_session(envelope, sca)
        durable_status = self._runtime.evidence_status(
            repo=envelope["repo"],
            environment=envelope["env"],
            artifact_digest=envelope["digest"],
        )
        completed = self._runtime.completed_evidence(
            repo=envelope["repo"],
            environment=envelope["env"],
            artifact_digest=envelope["digest"],
        )
        observation = envelope.get("runtimeObservation")
        runtime: dict[str, Any]
        if completed:
            session_ids = [str(item["manifest"]["sessionId"]) for item in completed]
            join_body = {
                "schemaVersion": "1.0.0",
                "artifactDigest": envelope["digest"],
                "sessionEvidence": completed,
            }
            runtime_ref = self._store.put(
                "runtime",
                f"{envelope['system']}/{run_id}/session-join",
                join_body,
                "1.0.0",
            )
            runtime = {
                "status": "VALIDATED",
                "source": "SESSION",
                "complete": True,
                "sessionIds": session_ids,
                "sessionEvidence": completed,
                "evidenceRef": runtime_ref.as_dict(),
            }
        elif observation is None:
            runtime = (
                {"status": "NOT_PROVIDED"}
                if durable_status["status"] == "NOT_PROVIDED"
                else dict(durable_status)
            )
        elif observation.get("artifactDigest") != envelope["digest"]:
            runtime = {"status": "REJECTED", "reason": "ARTIFACT_MISMATCH"}
        elif not observation.get("complete"):
            runtime = {"status": "INCOMPLETE", "reason": "SESSION_INCOMPLETE"}
        elif observation.get("scope") not in {"DATASET", "ELEMENT"}:
            runtime = {"status": "REJECTED", "reason": "INVALID_SCOPE"}
        elif not self._valid_runtime_assertions(observation.get("assertions")):
            runtime = {"status": "REJECTED", "reason": "INVALID_ASSERTIONS"}
        else:
            runtime_ref = self._store.put(
                "runtime",
                f"{envelope['system']}/{run_id}",
                observation,
                str(observation["schemaVersion"]),
            )
            runtime = {
                "status": "VALIDATED",
                "source": "EVENT_ADAPTER",
                "scope": observation["scope"],
                "complete": True,
                "sessionIds": [str(observation.get("sessionId", "event-adapter"))],
                "assertions": observation["assertions"],
                "evidenceRef": runtime_ref.as_dict(),
            }
        detail: dict[str, Any] = {
            "scaEvidenceRef": sca["scaRef"],
            "runtimeEvidenceStatus": runtime["status"],
        }
        if "evidenceRef" in runtime:
            detail["runtimeEvidenceRef"] = runtime["evidenceRef"]
        self._stage(run_id, "STORING_EVIDENCE", detail)
        if envelope.get("runtimeExecution") is True:
            runtime["reasons"] = reasons
            if verification is not None:
                runtime["verification"] = verification
        return {"runtime": runtime}

    def _execute_runtime_session(
        self, envelope: dict[str, Any], sca: dict[str, Any]
    ) -> tuple[str | None, list[str]]:
        """Produce runtime session evidence from the SCA claim. Fail-closed:
        every failure returns a closed-vocabulary reason and leaves SCA alone."""
        from lineage_api.application.java_runtime_stage import (
            java_home_or_none,
            run_java_runtime_stage,
        )
        from lineage_api.application.runtime_emission import sdk_payloads, session_scope
        from lineage_api.application.runtime_stage import (
            catalog_element_resolver,
            run_runtime_stage,
        )
        from lineage_api.domain.urns import LineageUrn
        from lineage_api.services.resolver import ResolveContext
        from lineage_api.services.runtime_verification import StaticEdge

        edges = [
            edge
            for edge in sca["sca"]["edges"]
            if "#" in str(edge.get("to", "")) and edge.get("from")
        ]
        if not edges:
            return None, ["execution-failed"]
        snapshot = self._snapshot_provider.resolve(envelope)
        read = lambda path: snapshot.read_bytes(path).decode()
        python_paths = [p for p in snapshot.paths if p.endswith(".py")]
        java_paths = [p for p in snapshot.paths if p.endswith(".java")]
        observed_at = self._clock()
        envelope_platform = LineageUrn.parse(str(edges[0]["to"])).platform
        observations: list[dict] = []
        verdicts: list[str] = []
        reasons: list[str] = []
        if python_paths and self._resolver is not None:
            try:
                stage = run_runtime_stage(
                    module_path=python_paths[0],
                    module_source=read(python_paths[0]),
                    static_edges=tuple(
                        StaticEdge(
                            str(e["from"][0]),
                            str(e["to"]),
                            str(e["edgeType"]),
                            str(e.get("transform", "")),
                        )
                        for e in edges
                    ),
                    resolve=catalog_element_resolver(
                        self._resolver,
                        ResolveContext(
                            env=envelope["env"],
                            platform=envelope_platform,
                            system=envelope["system"],
                            repo=envelope["repo"],
                            digest=envelope["digest"],
                            config={},
                            snapshot_id=self._resolver.snapshot_id,
                        ),
                    ),
                    observed_at=observed_at,
                    allow_execution=True,
                )
                observations += list(stage.observations)
                verdicts.append(stage.verdict)
            except Exception:
                reasons.append("execution-failed")
        if java_paths:
            java_home = java_home_or_none()
            if java_home is None:
                reasons.append("jvm-unavailable")
            else:
                try:
                    stage = run_java_runtime_stage(
                        sources={p: read(p) for p in java_paths},
                        static_edges=edges,
                        observed_at=observed_at,
                        java_home=java_home,
                    )
                    observations += list(stage.observations)
                    verdicts.append(stage.verdict)
                except Exception:
                    reasons.append("execution-failed")
        if not observations:
            return None, reasons or ["execution-failed"]
        payloads = sdk_payloads(
            observations,
            artifact_digest=envelope["digest"],
            run_id=str(envelope["eventId"]),
        )
        try:
            grant = self._runtime.grant_session(
                repo=envelope["repo"],
                environment=envelope["env"],
                artifact_digest=envelope["digest"],
                datasets=session_scope(observations),
                ttl_seconds=300,
                actor="collection-orchestrator",
            )
            session_id, token = str(grant["sessionId"]), str(grant["token"])
            self._runtime.mark_ready(session_id, token)
            for payload in payloads:
                self._runtime.observe(session_id, token, "SDK", payload)
            self._runtime.begin_drain(session_id, token)
            manifest = self._runtime.close(
                session_id,
                token,
                expected_observations=len(payloads),
                drained=True,
            )
        except DomainError as error:
            code = (
                "session-denied"
                if error.code == "RUNTIME_PRODUCTION_DENIED"
                else "observation-rejected"
            )
            return None, [*reasons, code]
        if manifest.get("outcome") != "COMPLETE":
            return None, [*reasons, "session-incomplete"]
        return _combined_verdict(verdicts), reasons

    @staticmethod
    def _valid_runtime_assertions(value: object) -> bool:
        if not isinstance(value, list):
            return False
        return all(
            isinstance(assertion, dict)
            and isinstance(assertion.get("provenanceId"), str)
            and isinstance(assertion.get("from"), list)
            and all(isinstance(item, str) for item in assertion["from"])
            and isinstance(assertion.get("to"), str)
            and isinstance(assertion.get("edgeType"), str)
            for assertion in value
        )

    def _incremental_consolidate(
        self,
        run_id: str,
        sca: dict[str, Any],
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        runtime_body = runtime["runtime"]
        runtime_by_edge: dict[tuple[tuple[str, ...], str, str], list[dict[str, Any]]] = {}
        if runtime_body["status"] == "VALIDATED" and "assertions" in runtime_body:
            for assertion in runtime_body["assertions"]:
                key = (
                    tuple(assertion.get("from", [])),
                    str(assertion.get("to", "")),
                    str(assertion.get("edgeType", "")),
                )
                runtime_by_edge.setdefault(key, []).append(assertion)

        run = self.get_run(run_id)
        merged = []
        for edge in sca["sca"]["edges"]:
            static = MechanismAssertion(
                provenance_id=edge["provenanceId"],
                from_urns=tuple(edge["from"]),
                to_urn=edge["to"],
                edge_type=edge["edgeType"],
                transform=edge.get("transform"),
                mechanism="SCA",
                exact=bool(edge["exact"]),
                evidence_ref=sca["scaRef"],
                repo=edge["repo"],
                run_id=edge["runId"],
                correlation_id=edge["correlationId"],
                citation=edge.get("evidence"),
            )
            assertions = [static]
            key = (tuple(edge["from"]), edge["to"], edge["edgeType"])
            for runtime_assertion in runtime_by_edge.get(key, []):
                assertions.append(
                    MechanismAssertion(
                        provenance_id=str(runtime_assertion["provenanceId"]),
                        from_urns=tuple(runtime_assertion["from"]),
                        to_urn=str(runtime_assertion["to"]),
                        edge_type=str(runtime_assertion["edgeType"]),
                        transform=runtime_assertion.get("transform"),
                        mechanism="RUNTIME",
                        exact=bool(runtime_assertion.get("exact", True)),
                        evidence_ref=runtime_body["evidenceRef"],
                        repo=edge["repo"],
                        run_id=edge["runId"],
                        correlation_id=edge["correlationId"],
                        runtime_scope=runtime_body["scope"],
                        session_complete=True,
                    )
                )
            merged.append(self._consolidation.merge_many(assertions))
        merged_by_key = {edge.edge_key: edge for edge in merged}
        for session in runtime_body.get("sessionEvidence", []):
            manifest = session["manifest"]
            for observation in session["observations"]:
                for runtime_edge in self._consolidation.merge_runtime_observation(
                    observation,
                    manifest,
                    environment=run["env"],
                    repo=run["repo"],
                    correlation_id=run["correlationId"],
                ):
                    merged_by_key[runtime_edge.edge_key] = runtime_edge
        merged = [merged_by_key[key] for key in sorted(merged_by_key)]
        edge_payloads = [edge.as_dict() for edge in merged]
        self._stage(
            run_id,
            "MERGING",
            {"edgeCount": len(edge_payloads), "bands": sorted({edge.band for edge in merged})},
        )
        return {"edges": edge_payloads}

    def _incremental_recheck(
        self,
        command: Command,
        start: dict[str, Any],
        plan: dict[str, Any],
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        pointer = self._publisher.pointer(start["environment"])
        if pointer.active_version != start["activeBaseVersion"]:
            raise DomainError(
                "STALE_BASE_VERSION",
                "Incremental base changed before proposal",
                command.correlation_id,
            )
        identity = {
            "artifactDigest": command.artifact_digest,
            "determinantDigest": command.determinant_digest,
            "expectedScope": plan["expectedScope"],
            "scopeDispositionDigest": plan["scopeDispositionDigest"],
            "runtimeEvidence": IncrementalWorkflow.runtime_coverage(runtime),
            "scope": command.scope,
        }
        manifest_id = f"coverage-{hashlib.sha256(self._canonical(identity).encode()).hexdigest()[:20]}"
        manifest = {
            "schemaVersion": "1.0.0",
            "manifestId": manifest_id,
            "workflowKind": command.workflow_kind,
            "scope": command.scope,
            "artifactDigest": command.artifact_digest,
            "determinantDigest": command.determinant_digest,
            "sourceScopeDispositionDigest": plan["scopeDispositionDigest"],
            "state": self._coverage_state(plan),
            "expectedScope": plan["expectedScope"],
            "completedScope": plan["recomputedScope"],
            "reusedScope": plan["reusedScope"],
            "skippedScope": plan["skippedScope"],
            "unsupportedScope": plan["unsupportedScope"],
            "quarantinedScope": [],
            "failedScope": plan["failedScope"],
            "runtimeEvidence": IncrementalWorkflow.runtime_coverage(runtime),
        }
        payload = self._canonical(manifest)
        now = self._clock()
        with self._database.transaction() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM coverage_manifests WHERE manifest_id = ?",
                (manifest_id,),
            ).fetchone()
            if existing is not None and existing["payload_json"] != payload:
                raise RuntimeError(f"coverage manifest conflict: {manifest_id}")
            connection.execute(
                """
                INSERT OR IGNORE INTO coverage_manifests(
                    manifest_id, workflow_kind, scope, artifact_digest,
                    determinant_digest, state, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest_id,
                    command.workflow_kind,
                    command.scope,
                    command.artifact_digest,
                    command.determinant_digest,
                    manifest["state"],
                    payload,
                    now,
                    now,
                ),
            )
        return {"coverageManifest": manifest}

    @staticmethod
    def _coverage_state(plan: dict[str, Any]) -> str:
        expected = list(plan["expectedScope"])
        dispositions = [
            list(plan["recomputedScope"]),
            list(plan.get("reusedScope", [])),
            list(plan["skippedScope"]),
            list(plan["unsupportedScope"]),
            list(plan["failedScope"]),
        ]
        flattened = [path for values in dispositions for path in values]
        accounted = sorted(flattened) == sorted(expected) and len(flattened) == len(
            set(flattened)
        )
        return (
            "COMPLETE"
            if accounted and not plan["unsupportedScope"] and not plan["failedScope"]
            else "INCOMPLETE"
        )

    def _incremental_propose(
        self,
        run_id: str,
        start: dict[str, Any],
        consolidation: dict[str, Any],
    ) -> dict[str, Any]:
        proposal = self._review.create(
            consolidation["edges"],
            start["activeBaseVersion"],
            self.get_run(run_id)["correlationId"],
        )
        self._stage(
            run_id,
            "PROPOSING",
            {"proposalId": proposal.proposal_id, "proposalVersion": proposal.version},
        )
        self._stage(run_id, "IN_REVIEW", {"proposalId": proposal.proposal_id})
        return {"proposal": proposal.as_dict()}

    @staticmethod
    def _incremental_finalize(
        command: Command,
        sca: dict[str, Any],
        runtime: dict[str, Any],
        consolidation: dict[str, Any],
        coverage: dict[str, Any],
        proposal: dict[str, Any],
    ) -> dict[str, Any]:
        edge_summary = [
            {
                "edgeKey": edge["edgeKey"],
                "version": edge["version"],
                "band": edge["band"],
                "status": edge["status"],
            }
            for edge in sorted(consolidation["edges"], key=lambda item: item["edgeKey"])
        ]
        return {
            "evidenceManifest": {
                "schemaVersion": "1.0.0",
                "commandId": command.command_id,
                "workflowKind": command.workflow_kind,
                "artifactDigest": command.artifact_digest,
                "determinantDigest": command.determinant_digest,
                "coverageManifestId": coverage["coverageManifest"]["manifestId"],
                "sca": sca["scaRef"],
                "runtime": runtime["runtime"],
                "edges": edge_summary,
                "proposal": {
                    "proposalId": proposal["proposal"]["proposalId"],
                    "version": proposal["proposal"]["version"],
                },
            }
        }

    def _event_envelope(self, command: Command) -> dict[str, Any]:
        if not command.input_ref.startswith("event://"):
            raise RuntimeError(f"invalid event reference {command.input_ref}")
        event_id = command.input_ref.removeprefix("event://")
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError(f"event {event_id} does not exist")
        return json.loads(row["payload_json"])

    def _result_for_completed_command(self, command: Command) -> dict[str, Any]:
        event_id = command.input_ref.removeprefix("event://")
        terminal = self._terminal_outcome(command)
        result = {
            "outcome": (
                str(terminal["outcome"])
                if terminal is not None
                else "REUSED"
            ),
            "reason": (
                terminal.get("reason")
                if terminal is not None
                else "COMMAND_ALREADY_COMPLETED"
            ),
            "eventId": event_id,
            "run": self._run_for_event(event_id),
            "proposal": self._proposal_for_event(event_id),
            "command": self._command_dict(command),
        }
        if command.workflow_kind in {"BASELINE", "INCREMENTAL"}:
            coverage_stage = "B8" if command.workflow_kind == "BASELINE" else "I8"
            evidence_stage = "B10" if command.workflow_kind == "BASELINE" else "I10"
            prefix = "B" if command.workflow_kind == "BASELINE" else "I"
            coverage = self._incremental_checkpoint(command, coverage_stage)
            evidence = self._incremental_checkpoint(command, evidence_stage)
            analysis = self._incremental_checkpoint(
                command, "B5" if command.workflow_kind == "BASELINE" else "I5"
            )
            if coverage is not None:
                result["coverageManifest"] = coverage["coverageManifest"]
            if evidence is not None:
                result["evidenceManifest"] = evidence["evidenceManifest"]
                result["runtimeStatus"] = evidence["evidenceManifest"]["runtime"]["status"]
            if analysis is not None:
                result["analysis"] = analysis["analysis"]
                if terminal is None and analysis["analysis"]["status"] != "COMPLETE":
                    result.update(
                        outcome="INTEGRATION_REQUIRED",
                        reason="INTEGRATION_REQUIRED",
                    )
            if command.workflow_kind == "INCREMENTAL" and evidence is None:
                runtime = self._incremental_checkpoint(command, "I6")
                if runtime is not None:
                    result["runtimeStatus"] = runtime["runtime"]["status"]
            result["resume"] = {
                "reusedStages": [f"{prefix}{index}" for index in range(1, 11)]
            }
        return result

    @staticmethod
    def _terminal_outcome(command: Command) -> dict[str, Any] | None:
        if command.output_ref is None:
            return None
        try:
            body = json.loads(command.output_ref)
        except (TypeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(body, dict)
            or body.get("schemaVersion") != "1.0.0"
            or not isinstance(body.get("outcome"), str)
            or body.get("reason") is not None
            and not isinstance(body.get("reason"), str)
        ):
            return None
        return body

    def _incremental_checkpoint(
        self,
        command: Command,
        stage_id: str,
    ) -> dict[str, Any] | None:
        stored = self._command_store.completed_stage(
            StageIdentity(
                workflow_kind=command.workflow_kind,
                scope=command.scope,
                artifact_digest=command.artifact_digest,
                stage_name=stage_id,
                determinant_digest=command.determinant_digest,
                schema_version=command.workflow_version,
            )
        )
        if stored is None:
            return None
        payload = json.loads(stored.output_ref)
        body = self._store.get(
            EvidenceRef(
                kind=payload["kind"],
                key=payload["key"],
                checksum=payload["checksum"],
                schema_version=payload["schemaVersion"],
            )
        )
        return body if isinstance(body, dict) else None

    @staticmethod
    def _command_dict(command: Command) -> dict[str, Any]:
        return {
            "commandId": command.command_id,
            "idempotencyKey": command.idempotency_key,
            "workflowKind": command.workflow_kind,
            "workflowVersion": command.workflow_version,
            "scope": command.scope,
            "artifactDigest": command.artifact_digest,
            "determinantDigest": command.determinant_digest,
            "status": command.status,
            "attempt": command.attempt,
            "maxAttempts": command.max_attempts,
            "inputRef": command.input_ref,
            "outputRef": command.output_ref,
            "correlationId": command.correlation_id,
            "createdAt": command.created_at.isoformat().replace("+00:00", "Z"),
            "deadlineAt": command.deadline_at.isoformat().replace("+00:00", "Z"),
        }

    def reconcile_runtime_evidence(
        self,
        *,
        repo: str,
        environment: str,
        artifact_digest: str,
        max_observations: int,
    ) -> dict[str, Any]:
        if max_observations < 1:
            raise ValueError("runtime reconciliation bound must be positive")
        evidence = self._runtime.completed_evidence(
            repo=repo,
            environment=environment,
            artifact_digest=artifact_digest,
        )
        observations = [
            (session["manifest"], observation)
            for session in evidence
            for observation in session["observations"]
        ]
        if not observations:
            return {
                "status": "NO_COMPLETE_RUNTIME",
                "sessionIds": [],
                "proposal": None,
            }
        if len(observations) > max_observations:
            raise DomainError(
                "RUNTIME_RECONCILIATION_BOUNDED",
                "Runtime reconciliation exceeds its observation bound",
                f"runtime-reconcile:{artifact_digest}",
                {"observations": len(observations), "limit": max_observations},
            )
        session_ids = sorted(
            {str(manifest["sessionId"]) for manifest, _ in observations}
        )
        identity = {
            "artifactDigest": artifact_digest,
            "environment": environment,
            "repo": repo,
            "sessionIds": session_ids,
        }
        correlation_id = (
            "runtime-reconcile-"
            + hashlib.sha256(self._canonical(identity).encode()).hexdigest()[:20]
        )
        reconciled: dict[str, Any] = {}
        for manifest, observation in observations:
            for edge in self._consolidation.merge_runtime_observation(
                observation,
                manifest,
                environment=environment,
                repo=repo,
                correlation_id=correlation_id,
            ):
                reconciled[edge.edge_key] = edge.as_dict()
        if not reconciled:
            return {
                "status": "NO_MATCHING_LINEAGE",
                "sessionIds": session_ids,
                "proposal": None,
            }
        pointer = self._publisher.pointer(environment)
        proposal = self._review.create(
            [reconciled[key] for key in sorted(reconciled)],
            pointer.active_version,
            correlation_id,
        )
        audit_id = f"audit-{hashlib.sha256(correlation_id.encode()).hexdigest()[:20]}"
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO audit_events(
                    audit_id, actor, action, resource_type, resource_id,
                    correlation_id, payload_json, created_at
                ) VALUES (?, 'system-runtime-reconciler', 'RUNTIME_RECONCILED',
                          'proposal', ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    proposal.proposal_id,
                    correlation_id,
                    self._canonical(identity),
                    self._clock(),
                ),
            )
        return {
            "status": "PROPOSED",
            "sessionIds": session_ids,
            "proposal": proposal.as_dict(),
            "edges": [reconciled[key] for key in sorted(reconciled)],
        }

    def approve(
        self,
        proposal_id: str,
        version: int,
        actor: str,
        rationale: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        decision = self._review.approve(
            proposal_id, version, actor, rationale, expected_lock_version
        )
        run = self._run_for_correlation(decision.proposal.correlation_id)
        self._stage(run["runId"], "PUBLISHING", {"approvalId": decision.approval.approval_id})
        published = self._publisher.publish(decision.proposal, decision.approval, env="staging")
        with self._database.connection() as connection:
            graph = connection.execute(
                "SELECT checksum FROM graph_versions WHERE env = ? AND version = ?",
                (run["env"], published.namespace_version),
            ).fetchone()
        if graph is None:  # pragma: no cover - publication read-back invariant
            raise RuntimeError("published graph namespace is unavailable")
        package_body = {
            "system": run["system"],
            "environment": run["env"],
            "artifactDigest": run["digest"],
            "graphVersion": published.namespace_version,
            "graphChecksum": graph["checksum"],
            "manifestRef": published.manifest_ref.as_dict(),
            "approvalRef": decision.approval.reference.as_dict(),
        }
        package_digest = "sha256:" + hashlib.sha256(
            self._canonical(package_body).encode()
        ).hexdigest()
        self._deployment_store.register_package(
            DeploymentPackage(
                package_digest=package_digest,
                system=run["system"],
                environment=run["env"],
                artifact_digest=run["digest"],
                graph_version=published.namespace_version,
                graph_checksum=graph["checksum"],
                manifest_ref=self._canonical(published.manifest_ref.as_dict()),
                approval_ref=self._canonical(decision.approval.reference.as_dict()),
                approved=True,
            )
        )
        finalized = self._review.finalize(proposal_id, version)
        self._stage(
            run["runId"],
            "PUBLISHED",
            {
                "namespaceVersion": published.namespace_version,
                "manifestRef": published.manifest_ref.as_dict(),
            },
        )
        return {
            "proposal": finalized.as_dict(),
            "pointer": self._pointer_dict(published.pointer),
            "approvalRef": decision.approval.reference.as_dict(),
            "manifestRef": published.manifest_ref.as_dict(),
            "run": self.get_run(run["runId"]),
        }

    def reject(
        self,
        proposal_id: str,
        version: int,
        actor: str,
        rationale: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        decision = self._review.reject(
            proposal_id, version, actor, rationale, expected_lock_version
        )
        run = self._run_for_correlation(decision.proposal.correlation_id)
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE runs SET state = 'REJECTED', updated_at = ? WHERE run_id = ?",
                (self._clock(), run["runId"]),
            )
        return {
            "proposal": decision.proposal.as_dict(),
            "approvalRef": decision.approval.reference.as_dict(),
            "run": self.get_run(run["runId"]),
        }

    def correct(
        self,
        proposal_id: str,
        version: int,
        actor: str,
        rationale: str,
        expected_lock_version: int,
        corrected_edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        proposal = self._review.correct(
            proposal_id,
            version,
            corrected_edges,
            actor,
            rationale,
            expected_lock_version,
        )
        return {"proposal": proposal.as_dict()}

    def list_runs(self) -> list[dict[str, Any]]:
        with self._database.connection() as connection:
            rows = connection.execute(
                "SELECT run_id FROM runs ORDER BY created_at DESC, run_id"
            ).fetchall()
        return [self.get_run(row["run_id"]) for row in rows]

    def operational_observations(self) -> dict[str, Any]:
        """Return local durable facts; policy and status labels live in observability."""
        with self._database.connection() as connection:
            queue_rows = connection.execute(
                """
                SELECT created_at AS queued_at FROM commands
                WHERE status IN ('QUEUED', 'RUNNING', 'RETRY_WAIT', 'FAILED_REDRIVABLE')
                UNION ALL
                SELECT published_at AS queued_at FROM lane_messages
                WHERE status IN ('AVAILABLE', 'IN_FLIGHT')
                UNION ALL
                SELECT created_at AS queued_at FROM outbox_events
                WHERE status = 'PENDING'
                """
            ).fetchall()
            retry_count = int(
                connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM commands WHERE attempt > 1) +
                        (SELECT COUNT(*) FROM lane_messages
                         WHERE attempts > 1 AND status != 'DEAD')
                    """
                ).fetchone()[0]
            )
            dead_letter_count = int(
                connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM lane_messages WHERE status = 'DEAD') +
                        (SELECT COUNT(*) FROM commands WHERE status = 'FAILED_TERMINAL')
                    """
                ).fetchone()[0]
            )
            lease_steal_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM command_attempts WHERE lease_epoch > 1"
                ).fetchone()[0]
            )
            coverage_rows = connection.execute(
                """
                SELECT workflow_kind, state, payload_json, updated_at
                FROM coverage_manifests ORDER BY updated_at, manifest_id
                """
            ).fetchall()
            oldest_approval = connection.execute(
                "SELECT MIN(created_at) FROM proposals WHERE state = 'IN_REVIEW'"
            ).fetchone()[0]
            in_progress_publication = connection.execute(
                """
                SELECT MIN(created_at) FROM publication_operations
                WHERE terminal_outcome IS NULL
                """
            ).fetchone()[0]
            completed_publication = connection.execute(
                """
                SELECT created_at, terminal_at FROM publication_operations
                WHERE terminal_outcome = 'SUCCEEDED'
                ORDER BY terminal_at DESC, operation_id DESC LIMIT 1
                """
            ).fetchone()
            correlation = connection.execute(
                """
                SELECT COUNT(*) AS tracked,
                       SUM(CASE WHEN correlation_id = '' THEN 1 ELSE 0 END) AS missing
                FROM (
                    SELECT correlation_id FROM runs
                    UNION ALL SELECT correlation_id FROM commands
                    UNION ALL SELECT correlation_id FROM lane_messages
                    UNION ALL SELECT correlation_id FROM deployment_events
                    UNION ALL SELECT correlation_id FROM publication_operations
                    UNION ALL SELECT correlation_id FROM audit_events
                    UNION ALL SELECT correlation_id FROM quarantines
                )
                """
            ).fetchone()

        coverage = []
        for row in coverage_rows:
            try:
                payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            runtime = payload.get("runtimeEvidence", {})
            coverage.append(
                {
                    "workflowKind": row["workflow_kind"],
                    "state": row["state"],
                    "runtimeStatus": runtime.get("status", "NOT_RECORDED"),
                    "updatedAt": row["updated_at"],
                }
            )
        completed_window = None
        if completed_publication is not None:
            completed_window = {
                "createdAt": completed_publication["created_at"],
                "terminalAt": completed_publication["terminal_at"],
            }
        return {
            "queueDepth": len(queue_rows),
            "oldestQueuedAt": min(
                (str(row["queued_at"]) for row in queue_rows), default=None
            ),
            "retryCount": retry_count,
            "deadLetterCount": dead_letter_count,
            "leaseStealCount": lease_steal_count,
            "coverage": coverage,
            "oldestApprovalAt": oldest_approval,
            "inProgressPublicationAt": in_progress_publication,
            "completedPublication": completed_window,
            "correlationTracked": int(correlation["tracked"] or 0),
            "correlationMissing": int(correlation["missing"] or 0),
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            stages = connection.execute(
                "SELECT * FROM run_stages WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        if row is None:
            raise DomainError(
                "RUN_NOT_FOUND", "Run does not exist", "unknown", {"runId": run_id}
            )
        return {
            "runId": row["run_id"],
            "eventId": row["event_id"],
            "repo": row["repo"],
            "digest": row["digest"],
            "env": row["env"],
            "system": row["system"],
            "state": row["state"],
            "correlationId": row["correlation_id"],
            "failedStage": row["failed_stage"],
            "errorCode": row["error_code"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "stages": [
                {
                    "sequence": stage["sequence"],
                    "stage": stage["stage"],
                    "status": stage["status"],
                    "correlationId": row["correlation_id"],
                    "detail": json.loads(stage["payload_json"]),
                    "startedAt": stage["started_at"],
                    "completedAt": stage["completed_at"],
                }
                for stage in stages
            ],
        }

    def _stage(self, run_id: str, stage: str, detail: dict[str, Any]) -> None:
        now = self._clock()
        with self._database.transaction() as connection:
            run = connection.execute(
                "SELECT correlation_id FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise DomainError("RUN_NOT_FOUND", "Run does not exist", "unknown")
            existing = connection.execute(
                "SELECT 1 FROM run_stages WHERE run_id = ? AND stage = ?",
                (run_id, stage),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                    (stage, now, run_id),
                )
                return
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_stages WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
            payload = {**detail, "correlationId": run["correlation_id"]}
            connection.execute(
                """
                INSERT INTO run_stages(
                    run_id, sequence, stage, status, payload_json, started_at, completed_at
                ) VALUES (?, ?, ?, 'COMPLETED', ?, ?, ?)
                """,
                (run_id, sequence, stage, self._canonical(payload), now, now),
            )
            connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                (stage, now, run_id),
            )

    def _fail(self, run_id: str, failed_stage: str, error_code: str) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE runs SET state = 'FAILED', failed_stage = ?, error_code = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (failed_stage, error_code, self._clock(), run_id),
            )

    def _run_for_event(self, event_id: str) -> dict[str, Any]:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT run_id FROM runs WHERE event_id = ?", (event_id,)
            ).fetchone()
        if row is None:
            raise DomainError("RUN_NOT_FOUND", "Accepted event has no run", f"corr-{event_id}")
        return self.get_run(row["run_id"])

    def _run_for_correlation(self, correlation_id: str) -> dict[str, Any]:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT run_id FROM runs WHERE correlation_id = ?", (correlation_id,)
            ).fetchone()
        if row is None:
            raise DomainError("RUN_NOT_FOUND", "Proposal has no originating run", correlation_id)
        return self.get_run(row["run_id"])

    def _proposal_for_event(self, event_id: str) -> dict[str, Any] | None:
        run = self._run_for_event(event_id)
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM proposals WHERE json_extract(payload_json, '$.correlationId') = ?
                ORDER BY version DESC LIMIT 1
                """,
                (run["correlationId"],),
            ).fetchone()
        return json.loads(row["payload_json"]) if row is not None else None

    @staticmethod
    def _runtime_fixture_document(sca: dict[str, Any]) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "sessionId": f"runtime-{sca['runId']}",
            "complete": True,
            "scope": "ELEMENT",
            "correlationId": sca["correlationId"],
            "assertions": [
                {
                    "provenanceId": f"runtime-{edge['provenanceId']}",
                    "from": edge["from"],
                    "to": edge["to"],
                    "edgeType": edge["edgeType"],
                }
                for edge in sca["edges"]
            ],
        }

    @staticmethod
    def _pointer_dict(pointer) -> dict[str, Any]:
        return {
            "env": pointer.env,
            "activeVersion": pointer.active_version,
            "fencingToken": pointer.fencing_token,
            "updatedAt": pointer.updated_at,
        }

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
