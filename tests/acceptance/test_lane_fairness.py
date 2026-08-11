from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from lineage_api.db import Database
from lineage_api.infrastructure.local_broker import LocalLaneBroker
from lineage_api.testing.evidence import AcceptanceEvidenceWriter


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 6, 12, tzinfo=UTC)


def test_interactive_lane_keeps_headroom_during_bounded_bulk_backlog(
    tmp_path: Path,
    acceptance_writer: AcceptanceEvidenceWriter,
) -> None:
    database = Database(tmp_path / "lane.db")
    database.initialize()
    broker = LocalLaneBroker(database, FixedClock())
    bulk_count = 300
    for index in range(bulk_count):
        broker.publish(
            "bulk",
            f"repo-{index % 10}",
            f"command://bulk-{index}",
            f"corr-bulk-{index}",
            message_id=f"bulk-{index}",
        )
    broker.publish(
        "interactive",
        "repo-pr",
        "command://pr-1",
        "corr-pr-1",
        message_id="pr-1",
    )

    with acceptance_writer.scenario(
        build_id="B04",
        acceptance_id="B04-AC-001",
        scenario_id="local-lane-headroom",
        environment="hermetic-local",
        thresholds={"interactiveClaimPosition": 1, "boundedBulkBacklog": bulk_count},
    ) as evidence:
        claim_order = []
        for lane in ("interactive", "events", "bulk"):
            message = broker.claim(lane, "acceptance-worker", visibility_timeout_seconds=30)
            if message is not None:
                claim_order.append(message)
                break
        assert claim_order[0].message_id == "pr-1"
        broker.acknowledge(claim_order[0])

        group_counts: dict[str, int] = {}
        for _ in range(20):
            message = broker.claim("bulk", "acceptance-worker", visibility_timeout_seconds=30)
            assert message is not None
            group_counts[message.group_key] = group_counts.get(message.group_key, 0) + 1
            broker.acknowledge(message)
        assert max(group_counts.values()) - min(group_counts.values()) <= 1
        evidence.metric("interactiveClaimPosition", 1)
        evidence.metric("boundedBulkBacklog", bulk_count)
        evidence.metric("bulkGroupsObserved", len(group_counts))
        claim_digest = hashlib.sha256(
            json.dumps(
                {"first": claim_order[0].message_id, "groups": group_counts},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        evidence.checksum("claimOrder", f"sha256:{claim_digest}")

    aws_manifest = acceptance_writer.record(
        build_id="B04",
        acceptance_id="B04-AC-001",
        scenario_id="aws-100ps-10k-burst",
        outcome="AWS_REQUIRED",
        environment="aws-ephemeral",
        metrics={"requiredSustainedTriggersPerSecond": 100, "requiredBurst": 10_000},
        thresholds={"p95Milliseconds": 500, "loss": 0, "duplicateEffects": 0},
    )
    assert aws_manifest["outcome"] == "AWS_REQUIRED"
