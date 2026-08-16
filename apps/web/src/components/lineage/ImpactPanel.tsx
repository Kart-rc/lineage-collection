import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { LineageEdge } from "../../api/types";
import { StatusPill } from "../shared/StatusPill";


const CHANGE_TYPES = [
  ["COLUMN_DROP", "Column drop"],
  ["COLUMN_TYPE_CHANGE", "Column type change"],
  ["DATASET_REMOVAL", "Dataset removal"],
  ["COLUMN_RENAME", "Column rename"],
  ["TRANSFORM_CHANGE", "Transform change"],
  ["FINGERPRINT_DRIFT", "Fingerprint drift"],
] as const;


export function ImpactPanel({ subject, edges }: { subject: string; edges: LineageEdge[] }) {
  const [changeType, setChangeType] = useState("COLUMN_DROP");
  const impact = useMutation({ mutationFn: () => api.impact(subject, changeType, 5) });
  const edgesByKey = useMemo(
    () => new Map(edges.map((edge) => [edge.edgeKey, edge])),
    [edges],
  );
  return (
    <section className="impact-panel" aria-labelledby="impact-heading">
      <div className="impact-panel__intro">
        <p className="eyebrow">Pre-change simulation</p>
        <h2 id="impact-heading">Downstream impact</h2>
        <p>Change the subject's schema — verdicts use the weakest confidence band on each bounded path.</p>
      </div>
      <form
        className="impact-form"
        onSubmit={(event) => {
          event.preventDefault();
          impact.mutate();
        }}
      >
        <label htmlFor="change-type">Change type</label>
        <select id="change-type" value={changeType} onChange={(event) => setChangeType(event.target.value)}>
          {CHANGE_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <button className="button button--primary" type="submit" disabled={impact.isPending || !subject}>
          {impact.isPending ? "Tracing consumers…" : "Run impact analysis"}
        </button>
      </form>
      {impact.error && (
        <p className="inline-error" role="alert">
          {impact.error instanceof ApiError ? impact.error.message : "Impact analysis failed."}
        </p>
      )}
      {impact.data && (
        <div className="impact-results">
          <div className="impact-summary" aria-label="Impact summary">
            <strong className="impact-summary__block" data-zero={impact.data.summary.block === 0}>
              {impact.data.summary.block} block
            </strong>
            <strong className="impact-summary__warn">{impact.data.summary.warn} warn</strong>
            <strong className="impact-summary__info">{impact.data.summary.info} info</strong>
            <span>Projection {impact.data.namespaceVersion} · depth {impact.data.depthSearched}</span>
          </div>
          <div className="impact-list">
            {impact.data.affected.map((item) => {
              const evidenceEdge = item.viaEdges
                .map((edgeKey) => edgesByKey.get(edgeKey))
                .find((edge): edge is LineageEdge => Boolean(edge));
              return (
                <article key={item.urn} data-severity={item.severity}>
                  <StatusPill
                    label={item.severity}
                    tone={item.severity === "BLOCK" ? "blocked" : item.severity === "WARN" ? "attention" : "neutral"}
                  />
                  <div>
                    <strong>{item.urn.split(":").at(-1) ?? item.urn}</strong>
                    <code>{item.urn}</code>
                  </div>
                  <dl>
                    <div><dt>Path</dt><dd>Path length {item.pathLength}</dd></div>
                    <div><dt>Confidence</dt><dd>{item.band} band</dd></div>
                    <div><dt>Corroboration</dt><dd>{item.corroboration}</dd></div>
                    <div><dt>Owner</dt><dd>{item.owner}</dd></div>
                  </dl>
                  {evidenceEdge && (
                    <code className="impact-evidence">
                      Evidence {evidenceEdge.provenance[0]?.evidenceRef.key}
                    </code>
                  )}
                </article>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
