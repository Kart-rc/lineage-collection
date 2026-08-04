from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.domain.evidence import EvidenceRef


ALLOWED_KINDS = {
    "approval",
    "catalog-snapshot",
    "llm",
    "llm-reject",
    "manifest",
    "proposal",
    "quarantine",
    "runtime",
    "sca",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class EvidenceStore:
    def __init__(
        self,
        database: Database,
        root: Path,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._database = database
        self._root = root
        self._clock = clock

    def put(
        self,
        kind: str,
        key: str,
        body: dict[str, Any] | list[Any] | bytes,
        schema_version: str,
    ) -> EvidenceRef:
        self._validate_location(kind, key)
        payload = self._canonical_bytes(body)
        checksum = hashlib.sha256(payload).hexdigest()
        reference = EvidenceRef(
            kind=kind,
            key=key,
            checksum=checksum,
            schema_version=schema_version,
        )
        path = self.path_for(reference)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            existing_checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            if existing_checksum != checksum:
                raise DomainError(
                    "OVERWRITE_ATTEMPT",
                    "Immutable evidence already exists with different content",
                    "system-evidence-store",
                    {"kind": kind, "key": key},
                )
        else:
            with path.open("xb") as handle:
                handle.write(payload)

        with self._database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT checksum, schema_version FROM evidence_objects
                WHERE kind = ? AND object_key = ?
                """,
                (kind, key),
            ).fetchone()
            if existing is not None and existing["checksum"] != checksum:
                raise DomainError(
                    "OVERWRITE_ATTEMPT",
                    "Immutable evidence index already points to different content",
                    "system-evidence-store",
                    {"kind": kind, "key": key},
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO evidence_objects(
                    kind, object_key, checksum, schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (kind, key, checksum, schema_version, self._clock()),
            )
        return reference

    def get(self, reference: EvidenceRef) -> Any:
        path = self.path_for(reference)
        payload = path.read_bytes()
        checksum = hashlib.sha256(payload).hexdigest()
        if checksum != reference.checksum:
            raise DomainError(
                "CHECKSUM_MISMATCH",
                "Evidence checksum verification failed",
                "system-evidence-store",
                {"kind": reference.kind, "key": reference.key},
            )
        return json.loads(payload)

    def path_for(self, reference: EvidenceRef) -> Path:
        self._validate_location(reference.kind, reference.key)
        path = self._root / reference.kind / f"{reference.key}.json"
        resolved_root = self._root.resolve()
        resolved_path = path.resolve()
        if resolved_root not in resolved_path.parents:
            raise DomainError(
                "INVALID_EVIDENCE_KEY",
                "Evidence key escapes the object root",
                "system-evidence-store",
            )
        return path

    def object_count(self) -> int:
        with self._database.connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM evidence_objects").fetchone()[0])

    @staticmethod
    def _canonical_bytes(body: dict[str, Any] | list[Any] | bytes) -> bytes:
        if isinstance(body, bytes):
            parsed = json.loads(body)
        else:
            parsed = body
        return json.dumps(
            parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()

    @staticmethod
    def _validate_location(kind: str, key: str) -> None:
        if kind not in ALLOWED_KINDS:
            raise DomainError(
                "UNKNOWN_EVIDENCE_KIND",
                f"Unknown evidence kind: {kind}",
                "system-evidence-store",
            )
        parts = Path(key).parts
        if not key or Path(key).is_absolute() or ".." in parts or "." in parts:
            raise DomainError(
                "INVALID_EVIDENCE_KEY",
                "Evidence key must be a safe relative path",
                "system-evidence-store",
            )
