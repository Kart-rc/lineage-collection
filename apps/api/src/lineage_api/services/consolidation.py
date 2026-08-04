from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from lineage_api.db import Database
from lineage_api.domain.confidence import derive_band, normalize_transform
from lineage_api.domain.evidence import EvidenceRef, ScaEdgeEvidence
from lineage_api.domain.urns import LineageUrn


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class MechanismAssertion:
    provenance_id: str
    from_urns: tuple[str, ...]
    to_urn: str
    edge_type: str
    transform: str | None
    mechanism: str
    exact: bool
    evidence_ref: dict[str, Any]
    repo: str
    run_id: str
    correlation_id: str
    citation: dict[str, Any] | None = None
    runtime_scope: str | None = None
    session_complete: bool = True

    def __post_init__(self) -> None:
        if self.mechanism not in {"SCA", "LLM", "RUNTIME"}:
            raise ValueError(f"Unknown mechanism: {self.mechanism}")
        if self.mechanism == "RUNTIME" and self.runtime_scope not in {"DATASET", "ELEMENT"}:
            raise ValueError("Runtime assertions require DATASET or ELEMENT scope")
        if self.mechanism != "RUNTIME" and self.runtime_scope is not None:
            raise ValueError("Only runtime assertions have a runtime scope")

    @classmethod
    def from_sca(
        cls, edge: ScaEdgeEvidence, reference: EvidenceRef
    ) -> "MechanismAssertion":
        return cls(
            provenance_id=edge.provenance_id,
            from_urns=(edge.from_urn,),
            to_urn=edge.to_urn,
            edge_type=edge.edge_type,
            transform=edge.transform,
            mechanism="SCA",
            exact=edge.exact,
            evidence_ref=reference.as_dict(),
            repo=edge.repo,
            run_id=edge.run_id,
            correlation_id=edge.correlation_id,
            citation={"file": edge.file, "line": edge.line, "astPath": edge.ast_path},
        )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provenanceId": self.provenance_id,
            "from": list(self.from_urns),
            "to": self.to_urn,
            "edgeType": self.edge_type,
            "mechanism": self.mechanism,
            "exact": self.exact,
            "evidenceRef": self.evidence_ref,
            "repo": self.repo,
            "runId": self.run_id,
            "correlationId": self.correlation_id,
            "sessionComplete": self.session_complete,
        }
        if self.transform is not None:
            payload["transform"] = self.transform
        if self.citation is not None:
            payload["citation"] = self.citation
        if self.runtime_scope is not None:
            payload["runtimeScope"] = self.runtime_scope
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MechanismAssertion":
        return cls(
            provenance_id=payload["provenanceId"],
            from_urns=tuple(payload["from"]),
            to_urn=payload["to"],
            edge_type=payload["edgeType"],
            transform=payload.get("transform"),
            mechanism=payload["mechanism"],
            exact=bool(payload["exact"]),
            evidence_ref=payload["evidenceRef"],
            repo=payload["repo"],
            run_id=payload["runId"],
            correlation_id=payload["correlationId"],
            citation=payload.get("citation"),
            runtime_scope=payload.get("runtimeScope"),
            session_complete=bool(payload.get("sessionComplete", True)),
        )


@dataclass(frozen=True, slots=True)
class ConsolidatedEdge:
    schema_version: str
    edge_key: str
    version: int
    from_urns: tuple[str, ...]
    to_urn: str
    edge_type: str
    band: str
    corroboration: str
    status: str
    transform: str | None
    provenance: tuple[MechanismAssertion, ...]
    auto_publishable: bool
    system: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "edgeKey": self.edge_key,
            "version": self.version,
            "from": list(self.from_urns),
            "to": self.to_urn,
            "edgeType": self.edge_type,
            "band": self.band,
            "corroboration": self.corroboration,
            "status": self.status,
            "provenance": [item.as_dict() for item in self.provenance],
            "autoPublishable": self.auto_publishable,
            "system": self.system,
            "updatedAt": self.updated_at,
        }
        if self.transform is not None:
            payload["transform"] = self.transform
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ConsolidatedEdge":
        return cls(
            schema_version=payload["schemaVersion"],
            edge_key=payload["edgeKey"],
            version=int(payload["version"]),
            from_urns=tuple(payload["from"]),
            to_urn=payload["to"],
            edge_type=payload["edgeType"],
            band=payload["band"],
            corroboration=payload["corroboration"],
            status=payload["status"],
            transform=payload.get("transform"),
            provenance=tuple(
                MechanismAssertion.from_dict(item) for item in payload["provenance"]
            ),
            auto_publishable=bool(payload["autoPublishable"]),
            system=payload["system"],
            updated_at=payload["updatedAt"],
        )


class ConsolidationService:
    def __init__(
        self,
        database: Database,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._database = database
        self._clock = clock

    def merge(self, assertion: MechanismAssertion) -> ConsolidatedEdge:
        edge_key = self.edge_key_for(assertion)
        with self._database.transaction() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM edge_ledger
                WHERE edge_key = ? ORDER BY version DESC LIMIT 1
                """,
                (edge_key,),
            ).fetchone()
            current = ConsolidatedEdge.from_dict(json.loads(row["payload_json"])) if row else None
            existing = current.provenance if current else ()
            if any(item.provenance_id == assertion.provenance_id for item in existing):
                return current  # type: ignore[return-value]

            provenance = tuple(
                sorted((*existing, assertion), key=lambda item: item.provenance_id)
            )
            version = (current.version + 1) if current else 1
            consolidated = self._derive(edge_key, version, provenance)
            connection.execute(
                """
                INSERT INTO edge_ledger(edge_key, version, system, status, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    edge_key,
                    version,
                    consolidated.system,
                    consolidated.status,
                    json.dumps(consolidated.as_dict(), sort_keys=True, separators=(",", ":")),
                    consolidated.updated_at,
                ),
            )
        return consolidated

    def merge_many(self, assertions: list[MechanismAssertion]) -> ConsolidatedEdge:
        if not assertions:
            raise ValueError("merge_many requires at least one assertion")
        edge_keys = {self.edge_key_for(assertion) for assertion in assertions}
        if len(edge_keys) != 1:
            raise ValueError("merge_many accepts assertions for exactly one edge")
        result: ConsolidatedEdge | None = None
        for assertion in assertions:
            result = self.merge(assertion)
        return result  # type: ignore[return-value]

    def version_count(self, edge_key: str) -> int:
        with self._database.connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM edge_ledger WHERE edge_key = ?", (edge_key,)
                ).fetchone()[0]
            )

    @staticmethod
    def edge_key_for(assertion: MechanismAssertion) -> str:
        identity = json.dumps(
            {
                "from": sorted(assertion.from_urns),
                "to": assertion.to_urn,
                "edgeType": assertion.edge_type,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"edge-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"

    def _derive(
        self,
        edge_key: str,
        version: int,
        provenance: tuple[MechanismAssertion, ...],
    ) -> ConsolidatedEdge:
        mechanisms: set[str] = set()
        for item in provenance:
            if item.mechanism != "RUNTIME":
                mechanisms.add(item.mechanism)
            elif item.session_complete and item.runtime_scope == "ELEMENT":
                mechanisms.add("RUNTIME")

        runtime_scopes = {
            item.runtime_scope
            for item in provenance
            if item.mechanism == "RUNTIME" and item.session_complete
        }
        if "ELEMENT" in runtime_scopes:
            corroboration = "ELEMENT"
        elif "DATASET" in runtime_scopes:
            corroboration = "DATASET"
        else:
            corroboration = "NONE"

        transform_assertions = [
            item
            for item in provenance
            if item.mechanism in {"SCA", "LLM"} and item.transform is not None
        ]
        normalized_transforms = {
            normalize_transform(item.transform) for item in transform_assertions if item.transform
        }
        conflicting = len(normalized_transforms) > 1
        selected = next(
            (
                item
                for item in provenance
                if item.mechanism == "SCA" and item.exact and item.transform is not None
            ),
            transform_assertions[0] if transform_assertions else None,
        )
        transform = None if conflicting or selected is None else selected.transform
        status = "CONFLICTING" if conflicting else "PROPOSED"
        auto_publishable = bool(
            not conflicting
            and any(
                item.mechanism == "SCA" and item.exact and item.transform is not None
                for item in provenance
            )
        )
        first = provenance[0]
        system = LineageUrn.parse(first.to_urn).system
        return ConsolidatedEdge(
            schema_version="1.0.0",
            edge_key=edge_key,
            version=version,
            from_urns=tuple(sorted(first.from_urns)),
            to_urn=first.to_urn,
            edge_type=first.edge_type,
            band=derive_band(mechanisms),
            corroboration=corroboration,
            status=status,
            transform=transform,
            provenance=provenance,
            auto_publishable=auto_publishable,
            system=system,
            updated_at=self._clock(),
        )
