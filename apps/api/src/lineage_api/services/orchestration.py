from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from lineage_api.application.models import Command, LaneMessage, StageIdentity, StageResult
from lineage_api.application.outbox import OutboxDispatcher
from lineage_api.application.ports import ClockPort, CommandStorePort, LaneBrokerPort
from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.services.classification import (
    ClassificationEvidence,
    ClassificationService,
)
from lineage_api.services.consolidation import ConsolidationService, MechanismAssertion
from lineage_api.services.evidence_store import EvidenceStore
from lineage_api.services.intake import IntakeService, PushDelivery
from lineage_api.services.publisher import PublisherService
from lineage_api.services.resolver import ResolveContext
from lineage_api.services.review import ReviewService
from lineage_api.services.sca import ScaAnalyzer


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class OrchestrationService:
    def __init__(
        self,
        database: Database,
        fixture_root: Path,
        intake: IntakeService,
        classification: ClassificationService,
        analyzer: ScaAnalyzer,
        store: EvidenceStore,
        consolidation: ConsolidationService,
        review: ReviewService,
        publisher: PublisherService,
        command_store: CommandStorePort,
        outbox_dispatcher: OutboxDispatcher,
        broker: LaneBrokerPort,
        durable_clock: ClockPort,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._database = database
        self._fixture_root = fixture_root
        self._intake = intake
        self._classification = classification
        self._analyzer = analyzer
        self._store = store
        self._consolidation = consolidation
        self._review = review
        self._publisher = publisher
        self._command_store = command_store
        self._outbox_dispatcher = outbox_dispatcher
        self._broker = broker
        self._durable_clock = durable_clock
        self._clock = clock

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
        if intake.outcome == "DUPLICATE":
            return {
                "outcome": "DUPLICATE",
                "reason": "DUPLICATE",
                "eventId": intake.event_id,
                "run": self._run_for_event(intake.event_id),
                "proposal": self._proposal_for_event(intake.event_id),
                "command": self._command_dict(
                    self._command_store.get(command.command_id) or command
                ),
            }

        processed = self.worker_once(classification_evidence=classification_evidence)
        if processed is None:
            raise RuntimeError(f"accepted command {command.command_id} was not drained")
        return processed

    def worker_once(
        self,
        classification_evidence: list[ClassificationEvidence] | None = None,
    ) -> dict[str, Any] | None:
        self._outbox_dispatcher.dispatch(limit=100)
        for lane in ("interactive", "events", "bulk"):
            message = self._broker.claim(
                lane,
                "local-worker",
                visibility_timeout_seconds=60,
            )
            if message is not None:
                return self._handle_message(message, classification_evidence)
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
        try:
            envelope = self._event_envelope(command)
            result = self._process_accepted(command, envelope, classification_evidence)
            output_ref = (
                f"proposal://{result['proposal']['proposalId']}"
                if result["proposal"] is not None
                else f"run://{result['run']['runId']}"
            )
            body = self._canonical(result).encode()
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
        except Exception:
            self._command_store.fail(lease, "WORKFLOW_EXECUTION_FAILED", retryable=True)
            self._broker.retry(
                message,
                available_at=self._durable_clock.now(),
                error_code="WORKFLOW_EXECUTION_FAILED",
            )
            raise

        self._broker.acknowledge(message)
        result["command"] = self._command_dict(completed)
        return result

    def _process_accepted(
        self,
        command: Command,
        envelope: dict[str, Any],
        classification_evidence: list[ClassificationEvidence] | None = None,
    ) -> dict[str, Any]:
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
            self._fixture_classification(envelope["repo"])
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

        repository_root = self._fixture_root / "repositories" / envelope["repo"]
        sca = self._analyzer.analyze(
            repository_root=repository_root,
            repo=envelope["repo"],
            digest=envelope["digest"],
            scope_paths=tuple(envelope["changedFiles"]),
            resolver_context=ResolveContext(
                env=envelope["env"],
                platform="snowflake",
                system=envelope["system"],
                repo=envelope["repo"],
                digest=envelope["digest"],
                config={},
                snapshot_id=self._analyzer._resolver.snapshot_id,
            ),
            run_id=run_id,
            correlation_id=correlation_id,
        )
        self._stage(
            run_id,
            "ANALYZING",
            {"filesAnalyzed": sca.stats["filesAnalyzed"], "edgesEmitted": len(sca.edges)},
        )
        self._stage(
            run_id,
            "RESOLVING",
            {"datasetsSeen": list(sca.datasets_seen), "residueCount": len(sca.residue)},
        )

        sca_ref = self._store.put(
            "sca", f"{envelope['system']}/{run_id}", sca.as_dict(), sca.schema_version
        )
        runtime_body = self._runtime_fixture(sca)
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
        for sca_edge in sca.edges:
            static_assertion = MechanismAssertion.from_sca(sca_edge, sca_ref)
            runtime_assertion = MechanismAssertion(
                provenance_id=f"runtime-{sca_edge.provenance_id}",
                from_urns=(sca_edge.from_urn,),
                to_urn=sca_edge.to_urn,
                edge_type=sca_edge.edge_type,
                transform=None,
                mechanism="RUNTIME",
                exact=True,
                evidence_ref=runtime_ref.as_dict(),
                repo=sca_edge.repo,
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
        return {
            "outcome": "REUSED",
            "reason": "COMMAND_ALREADY_COMPLETED",
            "eventId": event_id,
            "run": self._run_for_event(event_id),
            "proposal": self._proposal_for_event(event_id),
            "command": self._command_dict(command),
        }

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

    def _fixture_classification(self, repo: str) -> list[ClassificationEvidence]:
        path = self._fixture_root / "repositories" / repo / "repository-evidence.json"
        if not path.exists():
            return []
        body = json.loads(path.read_text(encoding="utf-8"))
        return [
            ClassificationEvidence(
                level=int(item["level"]),
                source=item["source"],
                repository_class=item["class"],
                ref=item["ref"],
            )
            for item in body["evidence"]
        ]

    @staticmethod
    def _runtime_fixture(sca) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0.0",
            "sessionId": f"runtime-{sca.run_id}",
            "complete": True,
            "scope": "ELEMENT",
            "correlationId": sca.correlation_id,
            "assertions": [
                {
                    "provenanceId": f"runtime-{edge.provenance_id}",
                    "from": [edge.from_urn],
                    "to": edge.to_urn,
                    "edgeType": edge.edge_type,
                }
                for edge in sca.edges
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
