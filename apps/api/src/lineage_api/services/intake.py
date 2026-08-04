from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Literal

from lineage_api.db import Database


LANE_POLICY_VERSION = "1.0.0"
LANE_POLICY = {
    "pr.updated": "interactive",
    "repo.push": "events",
    "deploy": "events",
    "baseline": "bulk",
    "backfill": "bulk",
    "nightly": "bulk",
    "llm.batch": "bulk",
}


@dataclass(frozen=True, slots=True)
class PushDelivery:
    payload: dict[str, Any]
    signature: str

    @property
    def canonical_body(self) -> bytes:
        return json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True, slots=True)
class IntakeResult:
    outcome: Literal["ACCEPTED", "DUPLICATE", "QUARANTINED"]
    event_id: str
    envelope: dict[str, Any] | None
    policy_version: str
    reason: str | None = None
    quarantine_id: str | None = None


class IntakeService:
    def __init__(self, database: Database, webhook_secret: str) -> None:
        self._database = database
        self._secret = webhook_secret.encode()

    def accept(self, delivery: PushDelivery) -> IntakeResult:
        payload = delivery.payload
        event_id = str(payload.get("eventId", ""))
        correlation_id = f"corr-{event_id or self._payload_digest(delivery.canonical_body)[:12]}"

        if not self._valid_signature(delivery):
            return self._quarantine(payload, event_id, correlation_id, "BAD_SIGNATURE")

        event_type = str(payload.get("eventType", ""))
        digest = str(payload.get("digest", ""))
        repo = str(payload.get("repo", ""))
        env = str(payload.get("env", ""))
        system = str(payload.get("system", ""))
        received_at = str(payload.get("receivedAt", ""))
        if not all((event_id, digest, repo, env, system, received_at)):
            return self._quarantine(payload, event_id, correlation_id, "MISSING_IDENTITY")
        if event_type not in LANE_POLICY:
            return self._quarantine(payload, event_id, correlation_id, "UNKNOWN_EVENT_TYPE")

        envelope = {
            "schemaVersion": "1.0.0",
            "eventId": event_id,
            "eventType": event_type,
            "correlationId": correlation_id,
            "repo": repo,
            "digest": digest,
            "env": env,
            "system": system,
            "lane": LANE_POLICY[event_type],
            "changedFiles": sorted(set(str(item) for item in payload.get("changedFiles", []))),
            "receivedAt": received_at,
        }
        envelope_json = json.dumps(envelope, sort_keys=True, separators=(",", ":"))

        with self._database.transaction() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if existing is not None:
                return IntakeResult(
                    outcome="DUPLICATE",
                    event_id=event_id,
                    envelope=json.loads(existing["payload_json"]),
                    policy_version=LANE_POLICY_VERSION,
                    reason="DUPLICATE",
                )
            connection.execute(
                """
                INSERT INTO events(event_id, payload_json, outcome, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, envelope_json, "ACCEPTED", received_at),
            )

        return IntakeResult(
            outcome="ACCEPTED",
            event_id=event_id,
            envelope=envelope,
            policy_version=LANE_POLICY_VERSION,
        )

    def event_count(self, event_id: str) -> int:
        with self._database.connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_id = ?", (event_id,)
                ).fetchone()[0]
            )

    def run_count_for(self, event_id: str) -> int:
        with self._database.connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE event_id = ?", (event_id,)
                ).fetchone()[0]
            )

    def quarantines(self) -> list[dict[str, Any]]:
        with self._database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM quarantines ORDER BY created_at, quarantine_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def _valid_signature(self, delivery: PushDelivery) -> bool:
        expected = hmac.new(self._secret, delivery.canonical_body, hashlib.sha256).hexdigest()
        provided = delivery.signature.removeprefix("sha256=")
        return hmac.compare_digest(expected, provided)

    def _quarantine(
        self,
        payload: dict[str, Any],
        event_id: str,
        correlation_id: str,
        reason: str,
    ) -> IntakeResult:
        safe_payload = {
            key: payload.get(key)
            for key in ("eventId", "eventType", "repo", "digest", "env", "system")
            if payload.get(key) not in (None, "")
        }
        safe_json = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"))
        identity = f"{reason}:{event_id}:{self._payload_digest(safe_json.encode())}"
        quarantine_id = f"q-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
        created_at = str(payload.get("receivedAt") or "1970-01-01T00:00:00Z")
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO quarantines(
                    quarantine_id, kind, reason, raw_json, candidates_json,
                    correlation_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quarantine_id,
                    "EVENT",
                    reason,
                    safe_json,
                    "[]",
                    correlation_id,
                    created_at,
                ),
            )
        return IntakeResult(
            outcome="QUARANTINED",
            event_id=event_id,
            envelope=None,
            policy_version=LANE_POLICY_VERSION,
            reason=reason,
            quarantine_id=quarantine_id,
        )

    @staticmethod
    def _payload_digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()
