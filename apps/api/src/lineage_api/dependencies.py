from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lineage_api.application.outbox import OutboxDispatcher
from lineage_api.application.repository_collection import (
    RepositoryCollectionService,
    RepositoryPushDelivery,
)
from lineage_api.application.repository_sources import RepositorySnapshot
from lineage_api.application.workflows.pr_gate import (
    EnvironmentPin,
    PRGateWorkflow,
    ProjectionUnavailableError,
)
from lineage_api.application.workflows.deployment import DeploymentWorkflow
from lineage_api.config import Settings
from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.infrastructure.local_broker import LocalLaneBroker, SQLiteOutbox
from lineage_api.infrastructure.sqlite_deployment import (
    HmacDeploymentAuthenticator,
    SQLiteDeploymentStore,
)
from lineage_api.infrastructure.sqlite_pr_gate import SQLitePrGateCheckStore
from lineage_api.infrastructure.sqlite_control import (
    SQLiteCommandStore,
    SQLiteIntakeUnitOfWork,
)
from lineage_api.observability import LocalMetricsSnapshot, MetricsSnapshotPort
from lineage_api.seed import SeedSummary, reset_demo
from lineage_api.services.classification import ClassificationService
from lineage_api.services.analyzer_registry import (
    AnalyzerRegistry,
    FixtureSnapshotProvider,
    PinnedSnapshotProvider,
)
from lineage_api.services.consolidation import ConsolidationService
from lineage_api.services.evidence_store import EvidenceStore
from lineage_api.services.intake import IntakeService
from lineage_api.services.orchestration import OrchestrationService
from lineage_api.services.publisher import PublisherService
from lineage_api.services.query import QueryService
from lineage_api.services.resolver import Resolver
from lineage_api.services.review import ReviewService
from lineage_api.services.runtime import RuntimeLineageService
from lineage_api.services.sca import ScaAnalyzer


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(slots=True)
class AppServices:
    settings: Settings
    database: Database
    evidence_store: EvidenceStore
    review: ReviewService
    publisher: PublisherService
    query: QueryService
    runtime: RuntimeLineageService
    orchestration: OrchestrationService
    repository_collection: RepositoryCollectionService
    pr_gate: PRGateWorkflow
    deployment: DeploymentWorkflow
    observability: MetricsSnapshotPort

    def reset(self) -> dict[str, Any]:
        if self.settings.object_directory.exists():
            shutil.rmtree(self.settings.object_directory)
        summary: SeedSummary = reset_demo(self.database, self.settings.fixture_directory)
        return {
            "activeVersion": summary.baseline_version,
            "catalogDigest": summary.catalog_digest,
            "catalogDatasetCount": summary.catalog_dataset_count,
            "demoDelivery": self.demo_delivery(),
        }

    def ensure_seeded(self) -> None:
        with self.database.connection() as connection:
            pointer = connection.execute(
                "SELECT 1 FROM pointers WHERE env = 'staging'"
            ).fetchone()
        if pointer is None:
            self.reset()

    def demo_delivery(self) -> dict[str, Any]:
        expected_lineage = json.loads(
            (
                self.settings.fixture_directory
                / "repositories"
                / "payments-pipeline"
                / "expected-lineage.json"
            ).read_text(encoding="utf-8")
        )
        payload = {
            "eventId": "delivery-demo-001",
            "eventType": "repo.push",
            "repo": "payments-pipeline",
            "digest": "demo-digest-v2",
            "env": "staging",
            "system": "payments",
            "changedFiles": ["pipeline.py"],
            "runtimeObservation": {
                "schemaVersion": "1.0.0",
                "artifactDigest": "demo-digest-v2",
                "complete": True,
                "scope": "ELEMENT",
                "assertions": [
                    {
                        "provenanceId": f"runtime-demo-{index}",
                        "from": [edge["from"]],
                        "to": edge["to"],
                        "edgeType": edge["type"],
                        "transform": edge["transform"],
                        "exact": True,
                    }
                    for index, edge in enumerate(expected_lineage["edges"], start=1)
                ],
            },
            "receivedAt": "2026-08-04T16:00:00Z",
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(
            self.settings.webhook_secret.encode(), canonical, hashlib.sha256
        ).hexdigest()
        return {"payload": payload, "signature": f"sha256={signature}"}


def build_services(
    settings: Settings,
    *,
    repository_snapshot: RepositorySnapshot | None = None,
) -> AppServices:
    database = Database(settings.database_path)
    database.initialize()
    clock = SystemClock()
    command_store = SQLiteCommandStore(database, clock)
    outbox = SQLiteOutbox(database, clock)
    broker = LocalLaneBroker(database, clock)
    intake = IntakeService(
        database,
        settings.webhook_secret,
        SQLiteIntakeUnitOfWork(database, command_store, outbox),
        clock,
    )
    resolver = Resolver.from_path(settings.fixture_directory / "catalog" / "catalog-snapshot-v1.json")
    python_analyzer = ScaAnalyzer(resolver, ruleset_version="python-demo-v1")
    analyzer_registry = AnalyzerRegistry.default(python_analyzer)
    snapshot_provider = (
        PinnedSnapshotProvider(repository_snapshot)
        if repository_snapshot is not None
        else FixtureSnapshotProvider(settings.fixture_directory)
    )
    store = EvidenceStore(database, settings.object_directory)
    review = ReviewService(database, store, env="staging")
    publisher = PublisherService(database, store)
    runtime = RuntimeLineageService(
        database,
        signing_secret=f"{settings.webhook_secret}:runtime",
        clock=clock,
        approved_otel_parsers=set(),
    )
    deployment_store = SQLiteDeploymentStore(
        database,
        clock=lambda: clock.now().isoformat().replace("+00:00", "Z"),
    )
    query = QueryService(database, env="staging")
    orchestration = OrchestrationService(
        database=database,
        intake=intake,
        classification=ClassificationService(database, policy_version="1.0.0"),
        analyzer_registry=analyzer_registry,
        snapshot_provider=snapshot_provider,
        store=store,
        consolidation=ConsolidationService(database),
        review=review,
        publisher=publisher,
        runtime=runtime,
        deployment_store=deployment_store,
        command_store=command_store,
        outbox_dispatcher=OutboxDispatcher(outbox, broker, clock),
        broker=broker,
        durable_clock=clock,
    )

    def environment_reader(environment: str) -> EnvironmentPin | None:
        with database.connection() as connection:
            row = connection.execute(
                """
                SELECT p.active_version, p.fencing_token, g.checksum
                FROM pointers p
                JOIN graph_versions g ON g.env = p.env AND g.version = p.active_version
                WHERE p.env = ?
                """,
                (environment,),
            ).fetchone()
        if row is None:
            return None
        return EnvironmentPin(
            environment=environment,
            graph_version=str(row["active_version"]),
            fencing_token=int(row["fencing_token"]),
            deployed_artifact_digest=f"sha256:{row['checksum']}",
            status="HEALTHY",
        )

    def impact_reader(change, environment, depth):
        try:
            return query.impact(
                change.subject,
                change.change_type,
                depth,
                environment.graph_version,
            )
        except DomainError as error:
            raise ProjectionUnavailableError(error.code) from error

    pr_gate = PRGateWorkflow(
        head_reader=lambda _repo, _pr_number, expected_head: expected_head,
        environment_reader=environment_reader,
        impact_reader=impact_reader,
        check_writer=SQLitePrGateCheckStore(database),
        monotonic=time.monotonic,
        utc_now=clock.now,
    )
    deployment = DeploymentWorkflow(
        store=deployment_store,
        publisher=publisher,
        authenticator=HmacDeploymentAuthenticator(settings.webhook_secret),
    )
    observability = LocalMetricsSnapshot(
        orchestration,
        query,
        clock=clock.now,
    )

    def process_repository_push(
        snapshot: RepositorySnapshot, delivery: RepositoryPushDelivery
    ) -> dict[str, Any]:
        if repository_snapshot is not None:
            if snapshot is not repository_snapshot:
                raise ValueError("repository snapshot does not match configured source")
            return orchestration.process_push(delivery)
        pinned = build_services(settings, repository_snapshot=snapshot)
        return pinned.orchestration.process_push(delivery)

    repository_collection = RepositoryCollectionService(
        webhook_secret=settings.webhook_secret,
        process_push=process_repository_push,
    )
    services = AppServices(
        settings=settings,
        database=database,
        evidence_store=store,
        review=review,
        publisher=publisher,
        query=query,
        runtime=runtime,
        orchestration=orchestration,
        repository_collection=repository_collection,
        pr_gate=pr_gate,
        deployment=deployment,
        observability=observability,
    )
    services.ensure_seeded()
    return services


def build_repository_collection_service(
    settings: Settings,
) -> RepositoryCollectionService:
    def process_repository_push(
        snapshot: RepositorySnapshot, delivery: RepositoryPushDelivery
    ) -> dict[str, Any]:
        services = build_services(settings, repository_snapshot=snapshot)
        return services.orchestration.process_push(delivery)

    return RepositoryCollectionService(
        webhook_secret=settings.webhook_secret,
        process_push=process_repository_push,
    )
