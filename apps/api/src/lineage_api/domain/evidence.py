from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: str
    key: str
    checksum: str
    schema_version: str

    def as_dict(self) -> dict[str, str]:
        return {
            "schemaVersion": self.schema_version,
            "kind": self.kind,
            "key": self.key,
            "checksum": self.checksum,
        }


@dataclass(frozen=True, slots=True)
class ScaEdgeEvidence:
    provenance_id: str
    from_urn: str
    to_urn: str
    edge_type: str
    transform: str
    mechanism: str
    exact: bool
    file: str
    line: int
    ast_path: str
    repo: str
    digest: str
    run_id: str
    correlation_id: str
    resolver_version: str
    snapshot_id: str

    @property
    def lineage_tuple(self) -> tuple[str, str, str, str]:
        return self.from_urn, self.to_urn, self.edge_type, self.transform

    def as_dict(self) -> dict[str, Any]:
        return {
            "provenanceId": self.provenance_id,
            "from": [self.from_urn],
            "to": self.to_urn,
            "edgeType": self.edge_type,
            "transform": self.transform,
            "mechanism": self.mechanism,
            "exact": self.exact,
            "evidence": {
                "file": self.file,
                "line": self.line,
                "astPath": self.ast_path,
            },
            "repo": self.repo,
            "digest": self.digest,
            "runId": self.run_id,
            "correlationId": self.correlation_id,
            "resolverVersion": self.resolver_version,
            "snapshotId": self.snapshot_id,
        }


@dataclass(frozen=True, slots=True)
class ResidueEntry:
    file: str
    line: int
    span: str
    reason: str
    symbol: str
    ast_path: str
    nearby_facts: tuple[str, ...]
    known_urns: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "file": payload["file"],
            "line": payload["line"],
            "span": payload["span"],
            "reason": payload["reason"],
            "symbol": payload["symbol"],
            "astPath": payload["ast_path"],
            "nearbyFacts": list(payload["nearby_facts"]),
            "knownUrns": list(payload["known_urns"]),
        }


@dataclass(frozen=True, slots=True)
class ScaEvidenceFile:
    schema_version: str
    repo: str
    digest: str
    run_id: str
    correlation_id: str
    ruleset_version: str
    resolver_version: str
    snapshot_id: str
    edges: tuple[ScaEdgeEvidence, ...]
    residue: tuple[ResidueEntry, ...]
    datasets_seen: tuple[str, ...]
    stats: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "repo": self.repo,
            "digest": self.digest,
            "runId": self.run_id,
            "correlationId": self.correlation_id,
            "rulesetVersion": self.ruleset_version,
            "resolverVersion": self.resolver_version,
            "snapshotId": self.snapshot_id,
            "edges": [edge.as_dict() for edge in self.edges],
            "residue": [entry.as_dict() for entry in self.residue],
            "datasetsSeen": list(self.datasets_seen),
            "stats": self.stats,
        }

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
