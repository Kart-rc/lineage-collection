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

    def merge_runtime_observation(
        self,
        observation: dict[str, Any],
        manifest: dict[str, Any],
        *,
        environment: str,
        repo: str,
        correlation_id: str,
    ) -> list[ConsolidatedEdge]:
        """Corroborate existing edges; runtime evidence never invents a new edge."""
        if manifest.get("outcome") != "COMPLETE":
            return []
        granularity = str(observation.get("granularity", ""))
        if granularity not in {"DATASET", "ELEMENT"}:
            return []
        source_datasets = tuple(
            self._runtime_dataset_urn(str(value), environment)
            for value in observation.get("sourceDatasets", [])
        )
        target_dataset = self._runtime_dataset_urn(
            str(observation.get("targetDataset", "")), environment
        )
        candidates = self._latest_edges()
        matched: list[ConsolidatedEdge] = []
        if granularity == "ELEMENT":
            source_fields = tuple(str(value) for value in observation.get("sourceFields", []))
            target_field = observation.get("targetField")
            if len(source_fields) != len(source_datasets) or not isinstance(target_field, str):
                return []
            expected_sources = tuple(
                str(LineageUrn.parse(dataset).with_element(field))
                for dataset, field in zip(source_datasets, source_fields, strict=True)
            )
            expected_target = str(LineageUrn.parse(target_dataset).with_element(target_field))
            candidates = [
                edge
                for edge in candidates
                if tuple(sorted(edge.from_urns)) == tuple(sorted(expected_sources))
                and edge.to_urn == expected_target
                and edge.edge_type == observation.get("edgeType")
            ]
        else:
            candidates = [
                edge
                for edge in candidates
                if {
                    LineageUrn.parse(value).dataset_urn for value in edge.from_urns
                }
                == set(source_datasets)
                and LineageUrn.parse(edge.to_urn).dataset_urn == target_dataset
                and edge.edge_type == observation.get("edgeType")
            ]

        for edge in candidates:
            suffix = "" if granularity == "ELEMENT" else f":{edge.edge_key}"
            assertion = MechanismAssertion(
                provenance_id=(
                    f"runtime:{manifest['sessionId']}:{observation['observationId']}{suffix}"
                ),
                from_urns=edge.from_urns,
                to_urn=edge.to_urn,
                edge_type=edge.edge_type,
                transform=observation.get("transform"),
                mechanism="RUNTIME",
                exact=bool(observation.get("exact", False)),
                evidence_ref={
                    "schemaVersion": "1.0.0",
                    "kind": "runtime",
                    "key": str(manifest["sessionId"]),
                    "checksum": str(manifest["observationChecksum"]),
                },
                repo=repo,
                run_id=f"runtime-{manifest['sessionId']}",
                correlation_id=correlation_id,
                runtime_scope=granularity,
                session_complete=True,
            )
            matched.append(self.merge(assertion))
        return sorted(matched, key=lambda edge: edge.edge_key)

    def version_count(self, edge_key: str) -> int:
        with self._database.connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM edge_ledger WHERE edge_key = ?", (edge_key,)
                ).fetchone()[0]
            )

    def _latest_edges(self) -> list[ConsolidatedEdge]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT ledger.payload_json
                FROM edge_ledger ledger
                JOIN (
                    SELECT edge_key, MAX(version) AS version
                    FROM edge_ledger GROUP BY edge_key
                ) latest
                  ON latest.edge_key = ledger.edge_key AND latest.version = ledger.version
                ORDER BY ledger.edge_key
                """
            ).fetchall()
        return [ConsolidatedEdge.from_dict(json.loads(row["payload_json"])) for row in rows]

    @staticmethod
    def _runtime_dataset_urn(value: str, environment: str) -> str:
        if "://" not in value:
            raise ValueError(f"invalid runtime dataset identifier: {value}")
        platform, remainder = value.split("://", 1)
        if "/" not in remainder:
            raise ValueError(f"invalid runtime dataset identifier: {value}")
        system, dataset = remainder.split("/", 1)
        return LineageUrn(environment, platform, system, dataset).dataset_urn

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
