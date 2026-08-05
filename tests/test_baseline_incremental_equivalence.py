from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from lineage_api.config import Settings
from lineage_api.services.intake import PushDelivery


PROJECT_ROOT = Path(__file__).parents[1]
SECRET = "differential-test-secret"


def settings(root: Path) -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_directory=root,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=root / "lineage.db",
        object_directory=root / "objects",
        webhook_secret=SECRET,
    )


def delivery(event_type: str, event_id: str) -> PushDelivery:
    payload = {
        "eventId": event_id,
        "eventType": event_type,
        "repo": "payments-pipeline",
        "digest": "demo-digest-v2",
        "env": "staging",
        "system": "payments",
        "changedFiles": ["pipeline.py"] if event_type == "repo.push" else [],
        "receivedAt": "2026-08-05T12:00:00Z",
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return PushDelivery(payload, f"sha256={signature}")


def normalized_affected_scope(result: dict[str, object]) -> dict[str, object]:
    coverage = result["coverageManifest"]
    evidence = result["evidenceManifest"]
    return {
        "affectedScope": coverage["completedScope"],
        "completedScope": coverage["completedScope"],
        "edges": sorted(
            (
                edge["edgeKey"],
                edge["band"],
                edge["status"],
            )
            for edge in evidence["edges"]
        ),
    }


def test_incremental_affected_scope_equals_clean_baseline_at_new_digest(
    tmp_path: Path,
) -> None:
    from lineage_api.dependencies import build_services

    baseline_services = build_services(settings(tmp_path / "baseline"))
    baseline_services.reset()
    baseline = baseline_services.orchestration.process_push(
        delivery("baseline", "delivery-baseline-differential")
    )

    incremental_services = build_services(settings(tmp_path / "incremental"))
    incremental_services.reset()
    incremental = incremental_services.orchestration.process_push(
        delivery("repo.push", "delivery-incremental-differential")
    )

    assert normalized_affected_scope(incremental) == normalized_affected_scope(baseline)
