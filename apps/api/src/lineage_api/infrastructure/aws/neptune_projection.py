from __future__ import annotations

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


class NeptuneProjectionAdapter:
    def __init__(self, client: Any) -> None:
        self.client = client

    def merge_edges(self, namespace: str, edges: list[dict[str, Any]], *, fence: int) -> int:
        ordered = sorted(edges, key=lambda edge: str(edge["edgeId"]))
        response = aws_call(
            "neptune.merge_edges",
            self.client.execute_open_cypher_query,
            openCypherQuery=_MERGE_QUERY,
            parameters={"namespace": namespace, "fence": fence, "edges": ordered},
        )
        results = response.get("results", [])
        return int(results[0].get("written", len(ordered))) if results else len(ordered)

    def namespace_checksum(self, namespace: str) -> list[dict[str, Any]]:
        response = aws_call(
            "neptune.read_namespace",
            self.client.execute_open_cypher_query,
            openCypherQuery=(
                "MATCH (source)-[edge:LINEAGE {namespace: $namespace}]->(target) "
                "RETURN edge.edgeId AS edgeId, source.urn AS source, target.urn AS target "
                "ORDER BY edgeId"
            ),
            parameters={"namespace": namespace},
        )
        return list(response.get("results", []))

