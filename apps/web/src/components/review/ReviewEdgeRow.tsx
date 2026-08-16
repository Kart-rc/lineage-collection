import type { LineageEdge } from "../../api/types";
import {
  bandDisplay,
  edgeCitation,
  edgeLabel,
  edgeSignals,
} from "./reviewMeta";

const SIGNAL_LABELS = { static: "Static", runtime: "Runtime", llm: "LLM" } as const;

type ReviewEdgeRowProps = {
  edge: LineageEdge;
  /** null = read-only (runtime-verified or historical) row rendered checked. */
  checked: boolean | null;
  onToggle?: (edgeKey: string) => void;
  selected?: boolean;
  marker?: "removed" | "band";
};

export function ReviewEdgeRow({
  edge,
  checked,
  onToggle,
  selected = false,
  marker,
}: ReviewEdgeRowProps) {
  const band = bandDisplay(edge.band);
  const citation = edgeCitation(edge);
  const readOnly = checked === null || !onToggle;
  const isChecked = checked !== false;
  const classes = [
    "review-edge",
    readOnly ? "review-edge--readonly" : "",
    marker === "removed" ? "review-edge--removed" : "",
    !marker && readOnly ? "review-edge--verified" : "",
    !marker && !readOnly && isChecked ? "review-edge--checked" : "",
    selected ? "review-edge--selected" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const body = (
    <>
      {marker ? (
        <span
          aria-hidden="true"
          className={`review-edge__marker review-edge__marker--${marker}`}
        >
          {marker === "removed" ? "−" : "~"}
        </span>
      ) : (
        <span aria-hidden="true" className="review-edge__box">
          {isChecked ? "✓" : ""}
        </span>
      )}
      <span className="review-edge__body">
        <span className="review-edge__top">
          <span
            className={`edge-type-chip edge-type-chip--${edge.edgeType === "WRITES" ? "writes" : "reads"}`}
          >
            {edge.edgeType}
          </span>
          <span className="review-edge__label" title={`${edge.from.join(", ")} → ${edge.to}`}>
            {edgeLabel(edge)}
          </span>
          <span className={`band-pill band-pill--${band.tone}`}>{band.label}</span>
        </span>
        <span className="review-edge__evidence">
          {citation ? <span className="review-edge__citation">{citation}</span> : null}
          {edge.transform ? (
            <span className="review-edge__transform" title={edge.transform}>
              {edge.transform}
            </span>
          ) : null}
          {edgeSignals(edge).map((signal) => (
            <span key={signal} className={`signal-chip signal-chip--${signal}`}>
              {SIGNAL_LABELS[signal]}
            </span>
          ))}
        </span>
      </span>
    </>
  );

  if (readOnly) {
    return (
      <div className={classes} data-edge-key={edge.edgeKey} id={`review-edge-${edge.edgeKey}`}>
        {body}
      </div>
    );
  }
  return (
    <button
      type="button"
      className={classes}
      id={`review-edge-${edge.edgeKey}`}
      aria-pressed={isChecked}
      aria-label={`Mark ${edge.edgeType.toLowerCase()} edge ${edgeLabel(edge)} as checked against its evidence`}
      onClick={() => onToggle(edge.edgeKey)}
    >
      {body}
    </button>
  );
}
