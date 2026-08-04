from __future__ import annotations

import json
from collections import deque
from typing import Any

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.domain.impact import BAND_ORDER, severity_for
from lineage_api.domain.urns import LineageUrn


class QueryService:
    def __init__(self, database: Database, env: str = "staging") -> None:
        self._database = database
        self._env = env

    def lineage(
        self,
        subject: str,
        direction: str = "down",
        depth: int = 3,
        version: str | None = None,
    ) -> dict[str, Any]:
        self._validate_depth(depth)
        if direction not in {"up", "down"}:
            raise DomainError(
                "INVALID_DIRECTION", "Direction must be up or down", "system-query"
            )
        namespace = self._resolve_version(version)
        all_edges = self._edges(namespace)
        frontier = {subject}
        nodes = {subject}
        selected: dict[str, dict[str, Any]] = {}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in all_edges:
                sources = set(edge["from"])
                destinations = {edge["to"]}
                matches = sources & frontier if direction == "down" else destinations & frontier
                if not matches:
                    continue
                selected[edge["edgeKey"]] = edge
                discovered = destinations if direction == "down" else sources
                next_frontier.update(discovered - nodes)
                nodes.update(sources | destinations)
            frontier = next_frontier
            if not frontier:
                break
        truncated = bool(frontier and self._has_continuation(frontier, all_edges, direction))
        return {
            "subject": subject,
            "direction": direction,
            "namespaceVersion": namespace,
            "depthSearched": depth,
            "truncated": truncated,
            "nodes": [self._node(urn) for urn in sorted(nodes)],
            "edges": [selected[key] for key in sorted(selected)],
        }

    def edge_detail(self, edge_key: str, version: str | None = None) -> dict[str, Any]:
        namespace = self._resolve_version(version)
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM graph_edges
                WHERE env = ? AND version = ? AND edge_key = ?
                """,
                (self._env, namespace, edge_key),
            ).fetchone()
        if row is None:
            raise DomainError(
                "EDGE_NOT_FOUND",
                f"Edge {edge_key} was not found in {namespace}",
                "system-query",
            )
        return json.loads(row["payload_json"])

    def impact(
        self,
        subject: str,
        change_type: str,
        depth: int = 5,
        version: str | None = None,
    ) -> dict[str, Any]:
        self._validate_depth(depth)
        namespace = self._resolve_version(version)
        edges = self._edges(namespace)
        queue: deque[tuple[str, int, str, list[str]]] = deque(
            [(subject, 0, "HIGHEST", [subject])]
        )
        best_depth = {subject: 0}
        affected: dict[str, dict[str, Any]] = {}
        truncated = False

        while queue:
            current, path_length, path_band, path = queue.popleft()
            outgoing = [edge for edge in edges if current in edge["from"]]
            if path_length >= depth:
                truncated = truncated or bool(outgoing)
                continue
            for edge in outgoing:
                target = edge["to"]
                next_length = path_length + 1
                next_band = min(
                    (path_band, edge["band"]), key=lambda band: BAND_ORDER[band]
                )
                if target in best_depth and best_depth[target] <= next_length:
                    continue
                best_depth[target] = next_length
                next_path = [*path, target]
                affected[target] = {
                    "urn": target,
                    "severity": severity_for(change_type, next_band),
                    "confidenceBand": next_band,
                    "pathLength": next_length,
                    "path": next_path,
                    "owner": f"team-{LineageUrn.parse(target).system}",
                }
                queue.append((target, next_length, next_band, next_path))

        ordered = sorted(affected.values(), key=lambda item: (item["pathLength"], item["urn"]))
        summary = {"block": 0, "warn": 0, "info": 0}
        for item in ordered:
            summary[item["severity"].lower()] += 1
        return {
            "subject": subject,
            "changeType": change_type,
            "namespaceVersion": namespace,
            "depthSearched": depth,
            "truncated": truncated,
            "affected": ordered,
            "summary": summary,
        }

    def _resolve_version(self, requested: str | None) -> str:
        with self._database.connection() as connection:
            if requested is None:
                row = connection.execute(
                    "SELECT active_version FROM pointers WHERE env = ?", (self._env,)
                ).fetchone()
                if row is None:
                    raise DomainError(
                        "POINTER_NOT_FOUND", "No active lineage projection", "system-query"
                    )
                return str(row["active_version"])
            exists = connection.execute(
                "SELECT 1 FROM graph_versions WHERE env = ? AND version = ?",
                (self._env, requested),
            ).fetchone()
        if exists is None:
            raise DomainError(
                "VERSION_NOT_FOUND",
                f"Projection version {requested} was not found",
                "system-query",
            )
        return requested

    def _edges(self, version: str) -> list[dict[str, Any]]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM graph_edges
                WHERE env = ? AND version = ? ORDER BY edge_key
                """,
                (self._env, version),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    @staticmethod
    def _validate_depth(depth: int) -> None:
        if depth < 1 or depth > 5:
            raise DomainError(
                "DEPTH_EXCEEDED",
                "Traversal depth must be between 1 and 5",
                "system-query",
                {"maximum": 5, "requested": depth},
            )

    @staticmethod
    def _has_continuation(
        frontier: set[str], edges: list[dict[str, Any]], direction: str
    ) -> bool:
        if direction == "down":
            return any(set(edge["from"]) & frontier for edge in edges)
        return any(edge["to"] in frontier for edge in edges)

    @staticmethod
    def _node(urn: str) -> dict[str, str]:
        parsed = LineageUrn.parse(urn)
        return {
            "urn": urn,
            "system": parsed.system,
            "kind": "ELEMENT" if parsed.element is not None else "DATASET",
        }
