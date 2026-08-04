from __future__ import annotations

import hashlib
import hmac
import json
import shutil
from dataclasses import dataclass
from typing import Any

from lineage_api.config import Settings
from lineage_api.db import Database
from lineage_api.seed import SeedSummary, reset_demo
from lineage_api.services.classification import ClassificationService
from lineage_api.services.consolidation import ConsolidationService
from lineage_api.services.evidence_store import EvidenceStore
from lineage_api.services.intake import IntakeService
from lineage_api.services.orchestration import OrchestrationService
from lineage_api.services.publisher import PublisherService
from lineage_api.services.query import QueryService
from lineage_api.services.resolver import Resolver
from lineage_api.services.review import ReviewService
from lineage_api.services.sca import ScaAnalyzer


@dataclass(slots=True)
class AppServices:
    settings: Settings
    database: Database
    evidence_store: EvidenceStore
    review: ReviewService
    publisher: PublisherService
    query: QueryService
    orchestration: OrchestrationService

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
        payload = {
            "eventId": "delivery-demo-001",
            "eventType": "repo.push",
            "repo": "payments-pipeline",
            "digest": "demo-digest-v2",
            "env": "staging",
            "system": "payments",
            "changedFiles": ["pipeline.py"],
            "receivedAt": "2026-08-04T16:00:00Z",
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(
            self.settings.webhook_secret.encode(), canonical, hashlib.sha256
        ).hexdigest()
        return {"payload": payload, "signature": f"sha256={signature}"}


def build_services(settings: Settings) -> AppServices:
    database = Database(settings.database_path)
    database.initialize()
    resolver = Resolver.from_path(settings.fixture_directory / "catalog" / "catalog-snapshot-v1.json")
    store = EvidenceStore(database, settings.object_directory)
    review = ReviewService(database, store, env="staging")
    publisher = PublisherService(database, store)
    query = QueryService(database, env="staging")
    orchestration = OrchestrationService(
        database=database,
        fixture_root=settings.fixture_directory,
        intake=IntakeService(database, settings.webhook_secret),
        classification=ClassificationService(database, policy_version="1.0.0"),
        analyzer=ScaAnalyzer(resolver, ruleset_version="python-demo-v1"),
        store=store,
        consolidation=ConsolidationService(database),
        review=review,
        publisher=publisher,
    )
    services = AppServices(settings, database, store, review, publisher, query, orchestration)
    services.ensure_seeded()
    return services
