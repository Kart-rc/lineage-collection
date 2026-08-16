import type { Provenance } from "../../api/types";

type ProvenancePanelProps = {
  provenance: Provenance[];
};

export function ProvenancePanel({ provenance }: ProvenancePanelProps) {
  return (
    <section className="provenance-panel" aria-labelledby="provenance-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Evidence</p>
          <h2 id="provenance-heading">Provenance assertions</h2>
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
            <strong>
              {item.citation
                ? `${item.citation.file.split("/").pop()}:${item.citation.line}`
                : item.runtimeScope
                  ? `Runtime scope · ${item.runtimeScope}`
                  : "Evidence assertion"}
            </strong>
            <p>
              {item.repo} · {item.runId}
              {item.sessionComplete ? " · complete session" : ""}
            </p>
            <dl>
              <div>
                <dt>Object</dt>
                <dd>
                  <code>{item.evidenceRef.key ?? "evidence"}</code>
                </dd>
              </div>
              <div>
                <dt>SHA-256</dt>
                <dd>
                  <code>
                    {String(
                      (item.evidenceRef as { sha256?: string; checksum?: string }).sha256 ??
                        item.evidenceRef.checksum ??
                        "unavailable",
                    ).slice(0, 16)}
                  </code>
                </dd>
              </div>
              <div>
                <dt>Correlation</dt>
                <dd>
                  <code>{item.correlationId}</code>
                </dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}
