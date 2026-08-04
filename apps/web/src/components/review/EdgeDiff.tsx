import type { LineageEdge } from "../../api/types";
import { StatusPill } from "../shared/StatusPill";


function displayName(urn: string) {
  return urn.split(":").at(-1)?.replace("#", " · ") ?? urn;
}


export function EdgeDiff({ edge, index }: { edge: LineageEdge; index: number }) {
  return (
    <article className="edge-diff">
      <div className="edge-diff__header">
        <span className="edge-diff__number">{String(index + 1).padStart(2, "0")}</span>
        <div>
          <p className="eyebrow">Added edge</p>
          <h3>{edge.edgeType.toLowerCase()}</h3>
        </div>
        <div className="edge-diff__badges">
          <StatusPill label={`${edge.band} confidence`} tone="trusted" />
          <StatusPill label={`${edge.corroboration} corroboration`} tone="neutral" />
        </div>
      </div>
      <div className="edge-diff__path">
        <div>
          <span>From</span>
          <strong>{displayName(edge.from[0])}</strong>
          <code>{edge.from[0]}</code>
        </div>
        <span className="edge-diff__arrow" aria-hidden="true">→</span>
        <div>
          <span>To</span>
          <strong>{displayName(edge.to)}</strong>
          <code>{edge.to}</code>
        </div>
      </div>
      {edge.transform && (
        <p className="transform-line"><span>Transform</span><code>{edge.transform}</code></p>
      )}
    </article>
  );
}
