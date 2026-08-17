from __future__ import annotations

import json
from collections import deque
from typing import Any

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.domain.impact import BAND_ORDER, severity_for
from lineage_api.domain.urns import LineageUrn


# The web explorer defaults to "both" and the Neptune adapter has always supported
# it, so the local backend accepting only up/down made the Lineage Explorer fail on
# first load. Keep the three values in one place so the two backends cannot drift.
DIRECTIONS = frozenset({"up", "down", "both"})


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
        if direction not in DIRECTIONS:
            raise DomainError(
                "INVALID_DIRECTION",
                "Direction must be up, down or both",
                "system-query",
            )
        namespace = self._resolve_version(version)
        all_edges = self._edges(namespace)
        frontier = {subject}
        nodes = {subject}
        selected: dict[str, dict[str, Any]] = {}
        # "both" walks each edge undirected, so an edge is followed whichever end
        # the frontier reached it from. Depth still bounds the walk, and `nodes`
        # keeps the frontier from revisiting, so an undirected pair cannot bounce
        # between its two ends forever.
        follow_down = direction in {"down", "both"}
        follow_up = direction in {"up", "both"}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in all_edges:
                sources = set(edge["from"])
                destinations = {edge["to"]}
                discovered: set[str] = set()
                if follow_down and sources & frontier:
                    discovered |= destinations
                if follow_up and destinations & frontier:
                    discovered |= sources
                if not discovered:
                    continue
                selected[edge["edgeKey"]] = edge
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
        corroboration_order = {"NONE": 0, "DATASET": 1, "ELEMENT": 2}
        queue: deque[tuple[str, int, str, str, list[str]]] = deque(
            [(subject, 0, "HIGHEST", "ELEMENT", [])]
        )
        best_depth = {subject: 0}
        affected: dict[str, dict[str, Any]] = {}
        truncated = False

        while queue:
            current, path_length, path_band, path_corroboration, via_edges = queue.popleft()
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
                next_corroboration = min(
                    (path_corroboration, edge["corroboration"]),
                    key=lambda value: corroboration_order[value],
                )
                if target in best_depth and best_depth[target] <= next_length:
                    continue
                best_depth[target] = next_length
                next_via_edges = [*via_edges, edge["edgeKey"]]
                parsed_target = LineageUrn.parse(target)
                affected[target] = {
                    "urn": target,
                    "system": parsed_target.system,
                    "severity": severity_for(change_type, next_band),
                    "band": next_band,
                    "corroboration": next_corroboration,
                    "pathLength": next_length,
                    "viaEdges": next_via_edges,
                    "owner": f"team-{parsed_target.system}",
                }
                queue.append(
                    (target, next_length, next_band, next_corroboration, next_via_edges)
                )

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

    def projection_observations(self) -> dict[str, Any]:
        """Read pointer/projection/deployment facts without assigning health policy."""
        with self._database.connection() as connection:
            pointer = connection.execute(
                """
                SELECT p.active_version, p.fencing_token, p.updated_at, g.checksum
                FROM pointers p
                LEFT JOIN graph_versions g
                  ON g.env = p.env AND g.version = p.active_version
                WHERE p.env = ?
                """,
                (self._env,),
            ).fetchone()
            deployment = connection.execute(
                """
                SELECT graph_version, lineage_package_digest, state, updated_at
                FROM deployment_state WHERE environment = ?
                ORDER BY updated_at DESC, system LIMIT 1
                """,
                (self._env,),
            ).fetchone()
        if pointer is None:
            return {
                "pointerPackageStatus": "NOT_AVAILABLE",
                "activeVersion": None,
                "packageVersion": None,
                "watermarkAt": None,
                "fencingToken": None,
                "checksum": None,
            }
        package_version = None if deployment is None else deployment["graph_version"]
        if pointer["checksum"] is None:
            pointer_package_status = "OUT_OF_SYNC"
        elif deployment is None:
            pointer_package_status = "NOT_AVAILABLE"
        elif (
            deployment["state"] == "LINEAGE_OUT_OF_SYNC"
            or package_version != pointer["active_version"]
            or deployment["lineage_package_digest"] is None
        ):
            pointer_package_status = "OUT_OF_SYNC"
        else:
            pointer_package_status = "IN_SYNC"
        return {
            "pointerPackageStatus": pointer_package_status,
            "activeVersion": pointer["active_version"],
            "packageVersion": package_version,
            "watermarkAt": pointer["updated_at"],
            "fencingToken": int(pointer["fencing_token"]),
            "checksum": pointer["checksum"],
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
        if direction == "up":
            return any(edge["to"] in frontier for edge in edges)
        return any(
            set(edge["from"]) & frontier or edge["to"] in frontier for edge in edges
        )

    @staticmethod
    def _node(urn: str) -> dict[str, str]:
        parsed = LineageUrn.parse(urn)
        return {
            "urn": urn,
            "system": parsed.system,
            "kind": "ELEMENT" if parsed.element is not None else "DATASET",
        }
