import { useMemo, useState } from "react";

import type { LineageEdge, LineageResponse } from "../../api/types";
import { bandDisplay, edgeLabel } from "../review/reviewMeta";
import type { CanvasCard } from "./lineageLayout";
import { buildCanvas, containerOf, nodeTitle } from "./lineageLayout";

export { nodeTitle };


interface LineageCanvasProps {
  data: LineageResponse;
  selectedEdgeKey: string | null;
  selectedNodeUrn?: string | null;
  onSelectEdge: (edge: LineageEdge) => void;
  onSelectNode: (urn: string) => void;
}


/** Weakest-band percentage shown at the end of a member row, prototype-style. */
function rowConfidence(band: string | null): { pct: string; tone: "verified" | "probable" } | null {
  if (!band) return null;
  const projection = bandDisplay(band);
  return { pct: `${/\d+/.exec(projection.label)?.[0] ?? ""}%`, tone: projection.tone };
}


export function LineageCanvas({
  data,
  selectedEdgeKey,
  selectedNodeUrn = null,
  onSelectEdge,
  onSelectNode,
}: LineageCanvasProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [allExpanded, setAllExpanded] = useState(true);

  const model = useMemo(
    () => buildCanvas(data.subject, data.nodes, data.edges, expanded, allExpanded),
    [data.subject, data.nodes, data.edges, expanded, allExpanded],
  );

  const selectedEdge = useMemo(
    () => data.edges.find((edge) => edge.edgeKey === selectedEdgeKey) ?? null,
    [data.edges, selectedEdgeKey],
  );

  // The neighborhood that stays lit while something is selected (prototype model).
  const litCards = useMemo(() => {
    if (selectedEdge) {
      return new Set(
        [...selectedEdge.from, selectedEdge.to].map((urn) => containerOf(urn)),
      );
    }
    if (selectedNodeUrn) {
      const lit = new Set([containerOf(selectedNodeUrn)]);
      for (const edge of data.edges) {
        const touches = [...edge.from, edge.to].includes(selectedNodeUrn);
        if (touches) for (const urn of [...edge.from, edge.to]) lit.add(containerOf(urn));
      }
      return lit;
    }
    return null;
  }, [data.edges, selectedEdge, selectedNodeUrn]);

  const edgeTouchesSelection = (edge: LineageEdge): boolean => {
    if (selectedEdgeKey) return edge.edgeKey === selectedEdgeKey;
    if (selectedNodeUrn) return [...edge.from, edge.to].includes(selectedNodeUrn);
    return false;
  };
  const hasSelection = Boolean(selectedEdgeKey || selectedNodeUrn);

  const toggleCard = (card: CanvasCard) => {
    setExpanded((state) => ({ ...state, [card.id]: !card.expanded }));
    if (card.selfUrn) onSelectNode(card.selfUrn);
  };

  return (
    <div className="explorer-canvas-shell">
      <div className="explorer-canvas-tools">
        <button
          type="button"
          onClick={() => {
            setExpanded({});
            setAllExpanded(true);
          }}
        >
          Expand all
        </button>
        <button
          type="button"
          onClick={() => {
            setExpanded({});
            setAllExpanded(false);
          }}
        >
          Collapse all
        </button>
      </div>

      <div
        className="explorer-canvas-scroll"
        role="group"
        aria-label={`Lineage graph in ${data.namespaceVersion}`}
      >
        <div
          className="explorer-canvas-plane"
          style={{ width: model.width, height: model.height }}
        >
          {model.columns.map(
            (column) =>
              column.label && (
                <span
                  key={column.x}
                  className="explorer-canvas-collabel"
                  style={{ left: column.x }}
                >
                  {column.label}
                </span>
              ),
          )}

          <svg
            className="explorer-canvas-wires"
            width={model.width}
            height={model.height}
            aria-hidden={model.edges.length === 0 || undefined}
          >
            {model.edges.map(({ edge, path, verified }) => {
              const active = edgeTouchesSelection(edge);
              const faded = hasSelection && !active;
              const stroke = active
                ? edge.edgeKey === selectedEdgeKey
                  ? "var(--brand)"
                  : verified
                    ? "oklch(45% 0.04 262)"
                    : "oklch(52% 0.15 75)"
                : verified
                  ? "oklch(55% 0.03 262)"
                  : "oklch(60% 0.13 75)";
              const width = (verified ? 2.2 : 1.3) + (active ? 0.8 : 0);
              const mechanisms = edge.provenance
                .map((item) => item.mechanism)
                .join(" + ");
              return (
                <g key={edge.edgeKey}>
                  <path
                    d={path}
                    fill="none"
                    stroke={stroke}
                    strokeWidth={width}
                    strokeOpacity={faded ? 0.3 : 1}
                    strokeDasharray={verified ? undefined : "3 4"}
                    pointerEvents="none"
                  />
                  <path
                    className="explorer-wire-hit"
                    d={path}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={12}
                    role="button"
                    tabIndex={0}
                    aria-label={`Inspect edge ${edgeLabel(edge)}, ${edge.band} confidence, ${mechanisms}`}
                    onClick={() => onSelectEdge(edge)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelectEdge(edge);
                      }
                    }}
                  >
                    <title>
                      {`${edgeLabel(edge)} · ${edge.edgeType} · ${bandDisplay(edge.band).label}`}
                    </title>
                  </path>
                </g>
              );
            })}
          </svg>

          {model.cards.map((card) => {
            const headerUrn = card.selfUrn;
            const dimmed = Boolean(litCards && !litCards.has(card.id));
            const headerSelected =
              (headerUrn !== null && headerUrn === selectedNodeUrn) ||
              (selectedNodeUrn !== null &&
                containerOf(selectedNodeUrn) === card.id &&
                headerUrn === selectedNodeUrn);
            const cardCarriesSelection =
              selectedNodeUrn !== null && containerOf(selectedNodeUrn) === card.id;
            return (
              <article
                key={card.id}
                className={`canvas-card${cardCarriesSelection ? " is-selected" : ""}${
                  card.isSubjectCard ? " canvas-card--subject" : ""
                }`}
                data-dimmed={dimmed || undefined}
                style={{ left: card.x, top: card.y, width: card.w, height: card.h }}
              >
                <button
                  type="button"
                  className="canvas-card__head"
                  aria-expanded={card.rows.length > 0 ? card.expanded : undefined}
                  aria-label={
                    headerUrn
                      ? `Select node ${nodeTitle(headerUrn)}, ${
                          card.role === "service" ? "SERVICE" : "DATASET"
                        }`
                      : `Toggle ${card.title}`
                  }
                  title={card.id}
                  onClick={() => toggleCard(card)}
                >
                  <span
                    className={`canvas-card__marker canvas-card__marker--${card.role}`}
                    aria-hidden="true"
                  />
                  <span className="canvas-card__idty">
                    <span
                      className={`canvas-card__name${headerSelected ? " is-selected" : ""}`}
                    >
                      {card.title}
                    </span>
                    <span className="canvas-card__meta">
                      {card.system} · {card.role}
                      {card.isSubjectCard ? " · subject" : ""}
                    </span>
                  </span>
                  {card.isSubjectCard && (
                    <span className="canvas-card__subject-chip">SUBJECT</span>
                  )}
                  {card.rows.length > 0 && (
                    <span className="canvas-card__caret" aria-hidden="true">
                      {card.expanded ? "▾" : "▸"}
                    </span>
                  )}
                </button>
                {card.expanded && (
                  <div className="canvas-card__rows">
                    {card.rows.map((row) => {
                      const confidence = rowConfidence(row.weakestBand);
                      const selected = row.urn === selectedNodeUrn;
                      return (
                        <button
                          key={row.urn}
                          type="button"
                          className={`canvas-row${selected ? " is-selected" : ""}`}
                          aria-label={`Select node ${nodeTitle(row.urn)}, ${row.kind}`}
                          title={row.urn}
                          onClick={() => onSelectNode(row.urn)}
                        >
                          <span
                            className={`canvas-row__dot canvas-row__dot--${row.kind.toLowerCase()}`}
                            aria-hidden="true"
                          />
                          <span className="canvas-row__name">{row.name}</span>
                          {confidence && (
                            <span
                              className={`canvas-row__conf canvas-row__conf--${confidence.tone}`}
                              title={
                                confidence.tone === "verified"
                                  ? "Weakest incident edge is runtime-verified"
                                  : "Weakest incident edge is static-only"
                              }
                            >
                              {confidence.pct}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      </div>

      <div className="explorer-legend-float" aria-label="Reading the graph">
        <span className="explorer-legend-float__title">Reading the graph</span>
        <span className="explorer-legend-float__item">
          <span className="explorer-legend-float__line explorer-legend-float__line--verified" />
          thick = verified at runtime
        </span>
        <span className="explorer-legend-float__item">
          <span className="explorer-legend-float__line explorer-legend-float__line--probable" />
          dashed amber = static-only
        </span>
        <span className="explorer-legend-float__item">
          arrows follow data flow · READS pulls, WRITES pushes
        </span>
        <span className="explorer-legend-float__item">
          <span className="explorer-legend-float__glyph explorer-legend-float__glyph--service" />
          service class ·{" "}
          <span className="explorer-legend-float__glyph explorer-legend-float__glyph--dataset" />
          dataset ·{" "}
          <span className="explorer-legend-float__glyph explorer-legend-float__glyph--element" />
          element
        </span>
        <span className="explorer-legend-float__item explorer-legend-float__item--hint">
          click a wire for its evidence · click a node to inspect it · Esc clears
        </span>
      </div>
    </div>
  );
}
