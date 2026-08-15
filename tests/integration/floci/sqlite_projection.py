"""SQLite implementation of the stage/impact projection ports for the floci E2E.

floci's Neptune emulation is a TinkerPop Gremlin server without the ``neptunedata``
openCypher REST API the production ``NeptuneProjectionAdapter`` requires (verified:
``POST /opencypher`` -> UnknownOperationException), so the projection port is the one
seam the emulator cannot honestly serve. This adapter mirrors the Neptune adapter's
semantics -- namespace-scoped edges, MERGE upserts keyed by (edgeId, source, target),
copy-on-publish namespaces, checksum row shape, band-aware downstream impact -- over
a local SQLite file so publication verification, deployment checksums and the PR-gate
impact traversal execute for real.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from lineage_api.domain.impact import BAND_ORDER, severity_for


class SqliteStageProjection:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(database_path))
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS edges (
                namespace TEXT NOT NULL,
                edge_id TEXT NOT NULL,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                type TEXT NOT NULL,
                band TEXT NOT NULL DEFAULT 'LOWEST',
                corroboration TEXT NOT NULL DEFAULT 'NONE',
                document TEXT,
                fence INTEGER NOT NULL,
                PRIMARY KEY (namespace, edge_id, source, target)
            )
            """
        )
        self._connection.commit()

    def merge_edges(
        self, namespace: str, edges: list[dict[str, Any]], *, fence: int
    ) -> int:
        ordered = sorted(edges, key=lambda edge: str(edge["edgeId"]))
        for edge in ordered:
            self._connection.execute(
                """
                INSERT INTO edges (namespace, edge_id, source, target, type, band,
                                   corroboration, document, fence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (namespace, edge_id, source, target) DO UPDATE SET
                    type = excluded.type,
                    fence = excluded.fence,
                    band = excluded.band,
                    corroboration = excluded.corroboration,
                    document = COALESCE(excluded.document, edges.document)
                """,
                (
                    namespace,
                    str(edge["edgeId"]),
                    str(edge["source"]),
                    str(edge["target"]),
                    str(edge["type"]),
                    str(edge.get("band", "LOWEST")),
                    str(edge.get("corroboration", "NONE")),
                    edge.get("document"),
                    fence,
                ),
            )
        self._connection.commit()
        return len(ordered)

    def copy_namespace(self, source: str, target: str, *, fence: int) -> int:
        if source == "NONE":
            return 0
        cursor = self._connection.execute(
            """
            INSERT INTO edges (namespace, edge_id, source, target, type, band,
                               corroboration, document, fence)
            SELECT ?, edge_id, source, target, type, band, corroboration, document, ?
            FROM edges WHERE namespace = ?
            ON CONFLICT (namespace, edge_id, source, target) DO UPDATE SET
                type = excluded.type,
                fence = excluded.fence,
                band = excluded.band,
                corroboration = excluded.corroboration,
                document = excluded.document
            """,
            (target, fence, source),
        )
        self._connection.commit()
        return cursor.rowcount

    def delete_edges(self, namespace: str, edge_ids: list[str]) -> int:
        ordered = sorted(set(edge_ids))
        if not ordered:
            return 0
        placeholders = ",".join("?" for _ in ordered)
        cursor = self._connection.execute(
            f"DELETE FROM edges WHERE namespace = ? AND edge_id IN ({placeholders})",
            (namespace, *ordered),
        )
        self._connection.commit()
        return cursor.rowcount

    def namespace_checksum(self, namespace: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT edge_id, source, target, type FROM edges "
            "WHERE namespace = ? ORDER BY edge_id",
            (namespace,),
        ).fetchall()
        return [
            {"edgeId": row[0], "source": row[1], "target": row[2], "type": row[3]}
            for row in rows
        ]

    def lineage(
        self,
        namespace: str,
        subject: str,
        *,
        direction: str,
        depth: int,
        limit: int,
    ) -> dict[str, Any]:
        if direction not in {"up", "down", "both"}:
            raise ValueError("lineage direction is invalid")
        if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= 5:
            raise ValueError("lineage depth must be between 1 and 5")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1_000:
            raise ValueError("lineage limit must be between 1 and 1000")
        rows = self._connection.execute(
            "SELECT edge_id, source, target, document FROM edges WHERE namespace = ?",
            (namespace,),
        ).fetchall()
        forward: dict[str, list[tuple[str, str, str]]] = {}
        backward: dict[str, list[tuple[str, str, str]]] = {}
        docs: dict[str, str] = {}
        for edge_id, source, target, document in rows:
            forward.setdefault(source, []).append((edge_id, target, document))
            backward.setdefault(target, []).append((edge_id, source, document))
            if document is not None:
                docs[edge_id] = document
        adjacency = {
            "down": [forward], "up": [backward], "both": [forward, backward]
        }[direction]
        urns = {subject}
        edge_documents: dict[str, dict[str, Any]] = {}
        truncated = False
        frontier = {subject}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for urn in frontier:
                for table in adjacency:
                    for edge_id, other, document in sorted(table.get(urn, [])):
                        if len(edge_documents) >= limit and edge_id not in edge_documents:
                            truncated = True
                            continue
                        if document is not None:
                            edge_documents.setdefault(edge_id, json.loads(document))
                        if other not in urns:
                            urns.add(other)
                            next_frontier.add(other)
            frontier = next_frontier
            if not frontier:
                break

        def node(urn: str) -> dict[str, Any]:
            if urn.startswith("service://"):
                return {"urn": urn, "system": urn.split("/", 3)[2], "kind": "DATASET"}
            try:
                from lineage_api.domain.urns import LineageUrn

                parsed = LineageUrn.parse(urn)
                return {
                    "urn": urn,
                    "system": parsed.system,
                    "kind": "ELEMENT" if parsed.element is not None else "DATASET",
                }
            except ValueError:
                return {"urn": urn, "system": "unknown", "kind": "DATASET"}

        return {
            "subject": subject,
            "direction": direction,
            "namespaceVersion": namespace,
            "depthSearched": depth,
            "truncated": truncated,
            "nodes": [node(urn) for urn in sorted(urns)],
            "edges": [edge_documents[key] for key in sorted(edge_documents)[:limit]],
        }

    def edge_detail(self, namespace: str, edge_key: str) -> dict[str, Any] | None:
        rows = self._connection.execute(
            "SELECT source, target, document FROM edges "
            "WHERE namespace = ? AND edge_id = ?",
            (namespace, edge_key),
        ).fetchall()
        if not rows:
            return None
        documents = {row[2] for row in rows if row[2] is not None}
        targets = {row[1] for row in rows}
        if len(documents) != 1 or len(targets) != 1:
            raise ValueError("projection edge detail topology is inconsistent")
        document = json.loads(next(iter(documents)))
        if not isinstance(document, dict) or document.get("edgeKey") != edge_key:
            raise ValueError("projection edge detail document is invalid")
        return document

    def impact(
        self,
        namespace: str,
        subject: str,
        change_type: str,
        *,
        depth: int,
        limit: int,
    ) -> dict[str, Any]:
        if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= 5:
            raise ValueError("impact depth must be between 1 and 5")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10_000:
            raise ValueError("impact limit must be between 1 and 10000")
        severity_for(change_type, "LOWEST")
        rows = self._connection.execute(
            "SELECT edge_id, source, target, band FROM edges WHERE namespace = ?",
            (namespace,),
        ).fetchall()
        downstream: dict[str, list[tuple[str, str, str]]] = {}
        for edge_id, source, target, band in rows:
            downstream.setdefault(source, []).append((edge_id, target, band))

        # Breadth-first downstream walk mirroring the Neptune adapter: shortest path
        # per affected URN wins; the path band is the weakest edge band on the path.
        affected_by_urn: dict[str, dict[str, Any]] = {}
        frontier: list[tuple[str, list[str], list[str]]] = [(subject, [], [])]
        truncated = False
        visited_paths = 0
        for _ in range(depth):
            next_frontier: list[tuple[str, list[str], list[str]]] = []
            for urn, via_edges, bands in frontier:
                for edge_id, target, band in sorted(downstream.get(urn, [])):
                    visited_paths += 1
                    if visited_paths > limit:
                        truncated = True
                        continue
                    path_edges = [*via_edges, edge_id]
                    path_bands = [*bands, band]
                    weakest = min(path_bands, key=lambda item: BAND_ORDER[item])
                    candidate = {
                        "urn": target,
                        "severity": severity_for(change_type, weakest),
                        "band": weakest,
                        "pathLength": len(path_edges),
                        "viaEdges": path_edges,
                    }
                    prior = affected_by_urn.get(target)
                    if prior is None or len(path_edges) < prior["pathLength"]:
                        affected_by_urn[target] = candidate
                    next_frontier.append((target, path_edges, path_bands))
            frontier = next_frontier
            if not frontier:
                break
        affected = sorted(
            affected_by_urn.values(), key=lambda item: (item["pathLength"], item["urn"])
        )
        summary = {"block": 0, "warn": 0, "info": 0}
        for item in affected:
            summary[str(item["severity"]).lower()] += 1
        return {
            "namespaceVersion": namespace,
            "subject": subject,
            "changeType": change_type,
            "depthSearched": depth,
            "truncated": truncated,
            "affected": affected,
            "summary": summary,
        }


__all__ = ["SqliteStageProjection"]
