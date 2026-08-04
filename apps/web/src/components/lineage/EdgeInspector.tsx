import type { LineageEdge } from "../../api/types";
import { StatusPill } from "../shared/StatusPill";


export function EdgeInspector({ edge }: { edge: LineageEdge | null }) {
  if (!edge) {
    return (
      <aside className="edge-inspector edge-inspector--empty">
        <p className="eyebrow">Selection</p>
        <h2>Edge evidence</h2>
        <p>Select a relationship in the graph to inspect its confidence and source record.</p>
      </aside>
    );
  }
  return (
    <aside className="edge-inspector">
      <p className="eyebrow">Selected relationship</p>
      <h2>Edge evidence</h2>
      <div className="edge-inspector__badges">
        <StatusPill label={`${edge.band} confidence`} tone="trusted" />
        <StatusPill label={`${edge.corroboration} corroboration`} />
      </div>
      <dl>
        <div><dt>Type</dt><dd>{edge.edgeType}</dd></div>
        <div><dt>Transform</dt><dd><code>{edge.transform ?? "Not asserted"}</code></dd></div>
        <div><dt>Edge key</dt><dd><code>{edge.edgeKey}</code></dd></div>
      </dl>
      <h3>Mechanism assertions</h3>
      <div className="inspector-provenance">
        {edge.provenance.map((item) => (
          <article key={item.provenanceId}>
            <span className={`mechanism mechanism--${item.mechanism.toLowerCase()}`}>{item.mechanism}</span>
            <strong>{item.citation ? `${item.citation.file}:${item.citation.line}` : item.runtimeScope}</strong>
            <code>{item.evidenceRef.checksum}</code>
          </article>
        ))}
      </div>
    </aside>
  );
}
