from __future__ import annotations

import json
from typing import Any

from lineage_api.infrastructure.aws.errors import aws_call


_MERGE_QUERY = """
UNWIND $edges AS row
MERGE (source:Dataset {urn: row.source})
MERGE (target:Dataset {urn: row.target})
MERGE (source)-[edge:LINEAGE {edgeId: row.edgeId, namespace: $namespace}]->(target)
SET edge.type = row.type, edge.fence = $fence
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
