import type { KeyboardEvent } from "react";

import type { LineageEdge, LineageResponse } from "../../api/types";
import { layoutGraph } from "./lineageLayout";


interface LineageCanvasProps {
  data: LineageResponse;
  selectedEdgeKey: string | null;
  onSelectEdge: (edge: LineageEdge) => void;
  onSelectNode: (urn: string) => void;
}


function shortName(urn: string) {
  const tail = urn.split(":").at(-1) ?? urn;
  const [dataset, element] = tail.split("#");
  return { dataset, element: element ?? "dataset" };
}


function activate(event: KeyboardEvent<SVGGElement>, action: () => void) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    action();
  }
}


export function LineageCanvas({
  data,
  selectedEdgeKey,
  onSelectEdge,
  onSelectNode,
}: LineageCanvasProps) {
  const layout = layoutGraph(data.nodes, data.edges, data.direction);
  return (
    <div className="lineage-canvas">
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width={layout.width}
        height={layout.height}
        role="group"
        aria-label={`Lineage graph in ${data.namespaceVersion}`}
      >
        <defs>
          <marker id="lineage-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
          <pattern id="lineage-grid" width="24" height="24" patternUnits="userSpaceOnUse">
            <path d="M 24 0 L 0 0 0 24" fill="none" />
          </pattern>
        </defs>
        <rect className="lineage-canvas__grid" width="100%" height="100%" />
        {data.edges.map((edge) => {
          const source = layout.nodes[edge.from[0]];
          const target = layout.nodes[edge.to];
          if (!source || !target) return null;
          const leftToRight = target.x > source.x;
          const x1 = source.x + (leftToRight ? source.width : 0);
          const x2 = target.x + (leftToRight ? 0 : target.width);
          const y1 = source.y + source.height / 2;
          const y2 = target.y + target.height / 2;
          const bend = Math.abs(x2 - x1) * 0.45;
          const path = `M ${x1} ${y1} C ${x1 + (leftToRight ? bend : -bend)} ${y1}, ${x2 + (leftToRight ? -bend : bend)} ${y2}, ${x2} ${y2}`;
          const mechanisms = edge.provenance.map((item) => item.mechanism).join(" + ");
          return (
            <g
              key={edge.edgeKey}
              className={selectedEdgeKey === edge.edgeKey ? "graph-edge is-selected" : "graph-edge"}
              role="button"
              tabIndex={0}
              aria-label={`Inspect edge ${edge.edgeKey}, ${edge.band} confidence, ${mechanisms}`}
              onClick={() => onSelectEdge(edge)}
              onKeyDown={(event) => activate(event, () => onSelectEdge(edge))}
            >
              <path className="graph-edge__target" d={path} />
              <path className="graph-edge__line" d={path} markerEnd="url(#lineage-arrow)" />
              <g transform={`translate(${(x1 + x2) / 2 - 34} ${(y1 + y2) / 2 - 13})`}>
                <rect className="graph-edge__label-bg" width="68" height="24" rx="12" />
                <text className="graph-edge__label" x="34" y="16" textAnchor="middle">{edge.band}</text>
              </g>
            </g>
          );
        })}
        {data.nodes.map((node) => {
          const position = layout.nodes[node.urn];
          const label = shortName(node.urn);
          return (
            <g
              key={node.urn}
              className="graph-node"
              role="button"
              tabIndex={0}
              aria-label={`Select node ${label.dataset} ${label.element}`}
              transform={`translate(${position.x} ${position.y})`}
              onClick={() => onSelectNode(node.urn)}
              onKeyDown={(event) => activate(event, () => onSelectNode(node.urn))}
            >
              <rect width={position.width} height={position.height} rx="8" />
              <text className="graph-node__kind" x="16" y="20">{node.system} / {node.kind}</text>
              <text className="graph-node__dataset" x="16" y="45">{label.dataset}</text>
              <text className="graph-node__element" x="16" y="64">#{label.element}</text>
            </g>
          );
        })}
      </svg>
      <div className="graph-fallback" aria-label="Accessible lineage relationships">
        <p>Relationships</p>
        <ul>
          {data.edges.map((edge) => (
            <li key={edge.edgeKey}>
              {shortName(edge.from[0]).element} → {shortName(edge.to).element}; {edge.band} confidence; {edge.provenance.map((item) => item.mechanism).join(" + ")}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
