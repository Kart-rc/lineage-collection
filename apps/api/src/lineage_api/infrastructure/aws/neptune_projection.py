from __future__ import annotations

import json
from typing import Any

from lineage_api.domain.impact import BAND_ORDER, severity_for
from lineage_api.domain.urns import LineageUrn
from lineage_api.infrastructure.aws.errors import aws_call


_MERGE_QUERY = """
UNWIND $edges AS row
MERGE (source:Dataset {urn: row.source})
MERGE (target:Dataset {urn: row.target})
MERGE (source)-[edge:LINEAGE {edgeId: row.edgeId, namespace: $namespace}]->(target)
SET edge.type = row.type, edge.fence = $fence,
    edge.band = coalesce(row.band, 'LOWEST'),
    edge.corroboration = coalesce(row.corroboration, 'NONE'),
    edge.document = coalesce(row.document, edge.document)
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
                "SET copy.document = edge.document "
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
        if direction == "down":
            pattern = (
                f"(subject:Dataset {{urn: $subject}})-"
                f"[edges:LINEAGE*1..{depth} {{namespace: $namespace}}]->(related:Dataset)"
            )
        elif direction == "up":
            pattern = (
                f"(subject:Dataset {{urn: $subject}})<-"
                f"[edges:LINEAGE*1..{depth} {{namespace: $namespace}}]-(related:Dataset)"
            )
        else:
            pattern = (
                f"(subject:Dataset {{urn: $subject}})-"
                f"[edges:LINEAGE*1..{depth} {{namespace: $namespace}}]-(related:Dataset)"
            )
        response = aws_call(
            "neptune.lineage",
            self.client.execute_open_cypher_query,
            openCypherQuery=(
                f"MATCH p={pattern} "
                "WITH p, edges ORDER BY size(edges), related.urn "
                f"LIMIT {limit + 1} "
                "RETURN [node IN nodes(p) | node.urn] AS urns, "
                "[edge IN edges | edge.edgeId] AS edgeIds, "
                "[edge IN edges | edge.document] AS documents"
            ),
            parameters=_parameters({"namespace": namespace, "subject": subject}),
        )
        rows = list(response.get("results", []))
        truncated = len(rows) > limit
        documents: dict[str, dict[str, Any]] = {}
        urns = {subject}
        for row in rows[:limit]:
            path_urns = row.get("urns", [])
            edge_ids = row.get("edgeIds", [])
            encoded_documents = row.get("documents", [])
            if (
                not isinstance(path_urns, list)
                or not 2 <= len(path_urns) <= depth + 1
                or not isinstance(edge_ids, list)
                or not isinstance(encoded_documents, list)
                or len(edge_ids) != len(path_urns) - 1
                or len(encoded_documents) != len(edge_ids)
            ):
                raise ValueError("Neptune returned an invalid lineage path")
            urns.update(str(value) for value in path_urns)
            for edge_id, encoded in zip(edge_ids, encoded_documents, strict=True):
                if not isinstance(encoded, str):
                    raise ValueError("Neptune lineage edge document is missing")
                document = json.loads(encoded)
                if not isinstance(document, dict) or document.get("edgeKey") != edge_id:
                    raise ValueError("Neptune lineage edge document is invalid")
                existing = documents.setdefault(str(edge_id), document)
                if existing != document:
                    raise ValueError("Neptune lineage edge document is inconsistent")
                if len(documents) > limit:
                    truncated = True
                    break
        ordered_urns = sorted(urns)
        nodes = []
        for urn in ordered_urns:
            parsed = LineageUrn.parse(urn)
            nodes.append(
                {
                    "urn": urn,
                    "system": parsed.system,
                    "kind": "ELEMENT" if parsed.element is not None else "DATASET",
                }
            )
        return {
            "subject": subject,
            "direction": direction,
            "namespaceVersion": namespace,
            "depthSearched": depth,
            "truncated": truncated,
            "nodes": nodes,
            "edges": [documents[key] for key in sorted(documents)[:limit]],
        }

    def edge_detail(self, namespace: str, edge_key: str) -> dict[str, Any] | None:
        response = aws_call(
            "neptune.edge_detail",
            self.client.execute_open_cypher_query,
            openCypherQuery=(
                "MATCH (source)-[edge:LINEAGE {namespace: $namespace, edgeId: $edgeId}]->(target) "
                "WITH collect(DISTINCT source.urn) AS sources, "
                "collect(DISTINCT target.urn) AS targets, "
                "collect(DISTINCT edge.document) AS documents "
                "RETURN sources, targets[0] AS target, documents[0] AS document, "
                "size(targets) AS targetCount, size(documents) AS documentCount"
            ),
            parameters=_parameters({"namespace": namespace, "edgeId": edge_key}),
        )
        rows = list(response.get("results", []))
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0].get("document"), str):
            raise ValueError("Neptune returned an invalid edge detail")
        document = json.loads(rows[0]["document"])
        if not isinstance(document, dict) or document.get("edgeKey") != edge_key:
            raise ValueError("Neptune edge detail document is invalid")
        sources = sorted(str(value) for value in rows[0].get("sources", []))
        if (
            not 1 <= len(sources) <= 64
            or rows[0].get("targetCount", 1) != 1
            or rows[0].get("documentCount", 1) != 1
            or document.get("from") != sources
            or document.get("to") != rows[0].get("target")
        ):
            raise ValueError("Neptune edge detail topology is inconsistent")
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
