import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { LineageEdge } from "../api/types";
import { EdgeInspector } from "../components/lineage/EdgeInspector";
import { ImpactPanel } from "../components/lineage/ImpactPanel";
import { LineageCanvas } from "../components/lineage/LineageCanvas";


const DEFAULT_SUBJECT = "urn:ldp:staging:snowflake:payments:raw.transactions#amount";


export function LineageExplorerPage() {
  const [subject, setSubject] = useState(DEFAULT_SUBJECT);
  const [draftSubject, setDraftSubject] = useState(DEFAULT_SUBJECT);
  const [direction, setDirection] = useState<"up" | "down">("down");
  const [depth, setDepth] = useState(3);
  const [selectedEdge, setSelectedEdge] = useState<LineageEdge | null>(null);
  const lineage = useQuery({
    queryKey: ["lineage", subject, direction, depth],
    queryFn: ({ signal }) => api.lineage(subject, direction, depth, signal),
  });

  return (
    <div className="page lineage-page">
      <header className="lineage-header">
        <div>
          <p className="eyebrow">Version-pinned graph</p>
          <h1>Lineage explorer</h1>
          <p className="lede">Trace what the platform knows, why it believes it, and what a change would affect.</p>
        </div>
        <span className="projection-stamp">
          {lineage.data ? `Projection ${lineage.data.namespaceVersion}` : "Resolving projection…"}
        </span>
      </header>
      <form
        className="lineage-controls"
        onSubmit={(event) => {
          event.preventDefault();
          setSubject(draftSubject.trim());
          setSelectedEdge(null);
        }}
      >
        <div className="control-field control-field--subject">
          <label htmlFor="lineage-subject">Subject URN</label>
          <input id="lineage-subject" value={draftSubject} onChange={(event) => setDraftSubject(event.target.value)} />
        </div>
        <div className="control-field">
          <label htmlFor="lineage-direction">Traversal direction</label>
          <select id="lineage-direction" value={direction} onChange={(event) => { setDirection(event.target.value as "up" | "down"); setSelectedEdge(null); }}>
            <option value="down">Downstream</option><option value="up">Upstream</option>
          </select>
        </div>
        <div className="control-field">
          <label htmlFor="lineage-depth">Traversal depth</label>
          <select id="lineage-depth" value={depth} onChange={(event) => { setDepth(Number(event.target.value)); setSelectedEdge(null); }}>
            {[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </div>
        <button className="button button--secondary" type="submit">Explore subject</button>
      </form>
      {lineage.isPending && <p className="empty-state">Resolving the active projection…</p>}
      {lineage.isError && <p className="inline-error" role="alert">Lineage could not be loaded for this subject.</p>}
      {lineage.data && (
        <>
          <div className="lineage-workbench">
            {lineage.data.edges.length ? (
              <LineageCanvas
                data={lineage.data}
                selectedEdgeKey={selectedEdge?.edgeKey ?? null}
                onSelectEdge={setSelectedEdge}
                onSelectNode={(urn) => setDraftSubject(urn)}
              />
            ) : (
              <div className="empty-state graph-empty">No relationships are present in {lineage.data.namespaceVersion}. Publish the seeded proposal first.</div>
            )}
            <EdgeInspector edge={selectedEdge} />
          </div>
          <ImpactPanel subject={subject} edges={lineage.data.edges} />
        </>
      )}
    </div>
  );
}
