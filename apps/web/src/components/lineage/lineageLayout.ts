import type { LineageEdge, LineageResponse } from "../../api/types";


export interface PositionedNode {
  x: number;
  y: number;
  width: number;
  height: number;
  layer: number;
}

export interface GraphLayout {
  width: number;
  height: number;
  nodes: Record<string, PositionedNode>;
}


export function layoutGraph(
  nodes: LineageResponse["nodes"],
  edges: LineageEdge[],
  direction: "up" | "down",
): GraphLayout {
  const ordered = [...nodes].sort((left, right) => left.urn.localeCompare(right.urn));
  const ranks = new Map(ordered.map((node) => [node.urn, 0]));
  const orderedEdges = [...edges].sort((left, right) => left.edgeKey.localeCompare(right.edgeKey));

  for (let pass = 0; pass < ordered.length; pass += 1) {
    let changed = false;
    for (const edge of orderedEdges) {
      const sourceRank = Math.max(...edge.from.map((source) => ranks.get(source) ?? 0));
      const nextRank = sourceRank + 1;
      if (nextRank > (ranks.get(edge.to) ?? 0)) {
        ranks.set(edge.to, nextRank);
        changed = true;
      }
    }
    if (!changed) break;
  }

  const maxRank = Math.max(0, ...ranks.values());
  const positioned: Record<string, PositionedNode> = {};
  ordered.forEach((node, index) => {
    const naturalRank = Math.min(ranks.get(node.urn) ?? 0, ordered.length - 1);
    const layer = direction === "down" ? naturalRank : maxRank - naturalRank;
    positioned[node.urn] = {
      x: 70 + layer * 280,
      y: 72 + index * 128,
      width: 220,
      height: 78,
      layer,
    };
  });
  return {
    width: Math.max(700, 150 + (maxRank + 1) * 280),
    height: Math.max(320, 130 + ordered.length * 128),
    nodes: positioned,
  };
}
