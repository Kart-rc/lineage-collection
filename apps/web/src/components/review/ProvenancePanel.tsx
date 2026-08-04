import type { Provenance } from "../../api/types";


export function ProvenancePanel({ provenance }: { provenance: Provenance[] }) {
  return (
    <section className="provenance-panel" aria-labelledby="provenance-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Immutable source record</p>
          <h2 id="provenance-heading">Provenance</h2>
        </div>
        <span>{provenance.length} assertions</span>
      </div>
      <div className="provenance-grid">
        {provenance.map((item) => (
          <article key={item.provenanceId} className="provenance-card">
            <div className="provenance-card__title">
              <span className={`mechanism mechanism--${item.mechanism.toLowerCase()}`}>
                {item.mechanism}
              </span>
              <span>{item.exact ? "Exact" : "Probable"}</span>
            </div>
            {item.citation ? (
              <strong>{item.citation.file}:{item.citation.line}</strong>
            ) : (
              <strong>{item.runtimeScope ?? "Evidence assertion"}</strong>
            )}
            <p>{item.citation?.astPath ?? `${item.repo} · complete session`}</p>
            <dl>
              <div><dt>Object</dt><dd>{item.evidenceRef.key}</dd></div>
              <div><dt>SHA-256</dt><dd><code>{item.evidenceRef.checksum}</code></dd></div>
              <div><dt>Correlation</dt><dd><code>{item.correlationId}</code></dd></div>
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}
