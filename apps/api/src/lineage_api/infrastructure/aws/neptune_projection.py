from __future__ import annotations

import json
from typing import Any

from lineage_api.domain.impact import BAND_ORDER, severity_for
from lineage_api.infrastructure.aws.errors import aws_call


_MERGE_QUERY = """
UNWIND $edges AS row
MERGE (source:Dataset {urn: row.source})
MERGE (target:Dataset {urn: row.target})
MERGE (source)-[edge:LINEAGE {edgeId: row.edgeId, namespace: $namespace}]->(target)
SET edge.type = row.type, edge.fence = $fence,
    edge.band = coalesce(row.band, 'LOWEST'),
    edge.corroboration = coalesce(row.corroboration, 'NONE')
RETURN count(edge) AS written
""".strip()


def _parameters(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class NeptuneProjectionAdapter:
    def __init__(self, client: Any) -> None:
        self.client = client

    def merge_edges(self, namespace: str, edges: list[dict[str, Any]], *, fence: int) -> int:
        ordered = sorted(edges, key=lambda edge: str(edge["edgeId"]))
        response = aws_call(
            "neptune.merge_edges",
            self.client.execute_open_cypher_query,
            openCypherQuery=_MERGE_QUERY,
            parameters=_parameters(
                {"namespace": namespace, "fence": fence, "edges": ordered}
            ),
        )
        results = response.get("results", [])
        return int(results[0].get("written", len(ordered))) if results else len(ordered)

    def copy_namespace(self, source: str, target: str, *, fence: int) -> int:
        if source == "NONE":
            return 0
        response = aws_call(
            "neptune.copy_namespace",
            self.client.execute_open_cypher_query,
            openCypherQuery=(
                "MATCH (source)-[edge:LINEAGE {namespace: $sourceNamespace}]->(target) "
                "MERGE (source)-[copy:LINEAGE {edgeId: edge.edgeId, "
                "namespace: $targetNamespace}]->(target) "
                "SET copy.type = edge.type, copy.fence = $fence "
                "SET copy.band = edge.band, copy.corroboration = edge.corroboration "
                "RETURN count(copy) AS written"
            ),
            parameters=_parameters(
                {
                    "sourceNamespace": source,
                    "targetNamespace": target,
                    "fence": fence,
                }
            ),
        )
        results = response.get("results", [])
        return int(results[0].get("written", 0)) if results else 0

    def delete_edges(self, namespace: str, edge_ids: list[str]) -> int:
        ordered = sorted(set(edge_ids))
        if not ordered:
            return 0
        response = aws_call(
            "neptune.delete_edges",
            self.client.execute_open_cypher_query,
            openCypherQuery=(
                "UNWIND $edgeIds AS edgeId "
                "MATCH ()-[edge:LINEAGE {namespace: $namespace, edgeId: edgeId}]->() "
                "DELETE edge RETURN count(edge) AS deleted"
            ),
            parameters=_parameters({"namespace": namespace, "edgeIds": ordered}),
        )
        results = response.get("results", [])
        return int(results[0].get("deleted", 0)) if results else 0

    def namespace_checksum(self, namespace: str) -> list[dict[str, Any]]:
        response = aws_call(
            "neptune.read_namespace",
            self.client.execute_open_cypher_query,
            openCypherQuery=(
                "MATCH (source)-[edge:LINEAGE {namespace: $namespace}]->(target) "
                "RETURN edge.edgeId AS edgeId, source.urn AS source, target.urn AS target, "
                "edge.type AS type "
                "ORDER BY edgeId"
            ),
            parameters=_parameters({"namespace": namespace}),
        )
        return list(response.get("results", []))

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
        response = aws_call(
            "neptune.impact",
            self.client.execute_open_cypher_query,
            openCypherQuery=(
                f"MATCH p=(source:Dataset {{urn: $subject}})-"
                f"[edges:LINEAGE*1..{depth} {{namespace: $namespace}}]"
                "->(target:Dataset) "
                "WITH target, edges ORDER BY size(edges), target.urn "
                f"LIMIT {limit + 1} "
                "RETURN target.urn AS urn, size(edges) AS pathLength, "
                "[edge IN edges | edge.edgeId] AS edgeIds, "
                "[edge IN edges | coalesce(edge.band, 'LOWEST')] AS bands"
            ),
            parameters=_parameters(
                {
                    "namespace": namespace,
                    "subject": subject,
                }
            ),
        )
        rows = list(response.get("results", []))
        truncated = len(rows) > limit
        affected_by_urn: dict[str, dict[str, Any]] = {}
        for row in rows[:limit]:
            urn = str(row["urn"])
            path_length = int(row["pathLength"])
            edge_ids = [str(item) for item in row.get("edgeIds", [])]
            bands = [str(item) for item in row.get("bands", [])]
            if (
                not 1 <= path_length <= depth
                or len(edge_ids) != path_length
                or len(bands) != path_length
                or any(band not in BAND_ORDER for band in bands)
            ):
                raise ValueError("Neptune returned an invalid impact path")
            band = min(bands, key=lambda item: BAND_ORDER[item])
            candidate = {
                "urn": urn,
                "severity": severity_for(change_type, band),
                "band": band,
                "pathLength": path_length,
                "viaEdges": edge_ids,
            }
            prior = affected_by_urn.get(urn)
            if prior is None or path_length < prior["pathLength"]:
                affected_by_urn[urn] = candidate
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
