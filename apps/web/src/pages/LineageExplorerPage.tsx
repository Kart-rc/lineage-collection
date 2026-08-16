import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { LineageDirection } from "../api/types";
import { EdgeInspector } from "../components/lineage/EdgeInspector";
import { ImpactPanel } from "../components/lineage/ImpactPanel";
import { LineageCanvas } from "../components/lineage/LineageCanvas";
import "../styles/pages/lineage.css";


const DEFAULT_SUBJECT = "urn:ldp:staging:mysql:petclinic-rest:owners";


export function LineageExplorerPage() {
  const [subject, setSubject] = useState(DEFAULT_SUBJECT);
  const [draftSubject, setDraftSubject] = useState(DEFAULT_SUBJECT);
  const [direction, setDirection] = useState<LineageDirection>("both");
  const [depth, setDepth] = useState(3);
  const [selectedEdgeKey, setSelectedEdgeKey] = useState<string | null>(null);
  const [selectedNodeUrn, setSelectedNodeUrn] = useState<string | null>(null);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const lineage = useQuery({
    queryKey: ["lineage", subject, direction, depth],
    queryFn: ({ signal }) => api.lineage(subject, direction, depth, signal),
  });
  const selectedEdge =
    lineage.data?.edges.find((edge) => edge.edgeKey === selectedEdgeKey) ?? null;

  function clearSelection() {
    setSelectedEdgeKey(null);
    setSelectedNodeUrn(null);
  }

  // Prototype interaction: Escape clears the current selection.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") clearSelection();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function reRoot(urn: string) {
    setDraftSubject(urn);
    setSubject(urn);
    clearSelection();
  }

  return (
    <div className="lineage-page">
      <h1 className="explorer-sr">Lineage explorer</h1>
      <form
        className="explorer-toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          const next = draftSubject.trim();
          if (!next) return;
          setSubject(next);
          clearSelection();
        }}
      >
        <div className="explorer-subject">
          <label htmlFor="lineage-subject">Subject URN</label>
          <input
            id="lineage-subject"
            value={draftSubject}
            onChange={(event) => setDraftSubject(event.target.value)}
            spellCheck={false}
          />
        </div>
        <label className="explorer-sr" htmlFor="lineage-direction">
          Traversal direction
        </label>
        <select
          id="lineage-direction"
          className="explorer-select"
          value={direction}
          onChange={(event) => {
            setDirection(event.target.value as LineageDirection);
            clearSelection();
          }}
        >
          <option value="both">Both directions</option>
          <option value="up">Upstream only</option>
          <option value="down">Downstream only</option>
        </select>
        <label className="explorer-sr" htmlFor="lineage-depth">
          Traversal depth
        </label>
        <select
          id="lineage-depth"
          className="explorer-select"
          value={depth}
          onChange={(event) => {
            setDepth(Number(event.target.value));
            clearSelection();
          }}
        >
          {[1, 2, 3, 4, 5].map((value) => (
            <option key={value} value={value}>
              Depth {value}
            </option>
          ))}
        </select>
        <button className="explorer-go" type="submit" disabled={lineage.isFetching}>
          {lineage.isFetching ? "Exploring…" : "Explore"}
        </button>
        <span className="explorer-stamp">
          {lineage.data
            ? `Projection ${lineage.data.namespaceVersion} · depth ${lineage.data.depthSearched}`
            : lineage.isError
              ? "No projection resolved"
              : "Resolving projection…"}
        </span>
      </form>

      <div className="explorer-body">
        <div className="explorer-main">
          {lineage.data?.truncated && (
            <p className="lineage-truncated" role="status">
              The walk was truncated at the result limit — the graph shown is not the complete
              neighborhood. Reduce depth or re-root closer to the subject you care about.
            </p>
          )}
          {lineage.isPending && <p className="empty-state">Resolving the active projection…</p>}
          {lineage.isError && (
            <p className="inline-error" role="alert">
              Lineage could not be loaded for this subject. Check that the URN is complete, e.g.{" "}
              <code>{DEFAULT_SUBJECT}</code>.
            </p>
          )}
          {lineage.data &&
            (lineage.data.nodes.length ? (
              <>
                <LineageCanvas
                  data={lineage.data}
                  selectedEdgeKey={selectedEdgeKey}
                  selectedNodeUrn={selectedNodeUrn}
                  onSelectEdge={(edge) => {
                    setSelectedEdgeKey(edge.edgeKey);
                    setSelectedNodeUrn(null);
                    setRailCollapsed(false);
                  }}
                  onSelectNode={(urn) => {
                    setSelectedNodeUrn(urn);
                    setSelectedEdgeKey(null);
                    setRailCollapsed(false);
                  }}
                />
                {lineage.data.edges.length === 0 && (
                  <p className="empty-state">
                    No relationships within depth {lineage.data.depthSearched} — this subject is
                    isolated in {lineage.data.namespaceVersion}.
                  </p>
                )}
              </>
            ) : (
              <p className="empty-state">
                Nothing is published for this subject in {lineage.data.namespaceVersion}. Approve
                a proposal in the Review queue first.
              </p>
            ))}
          {lineage.data && (
            <ImpactPanel key={subject} subject={subject} edges={lineage.data.edges} />
          )}
        </div>
        {lineage.data && (
          <EdgeInspector
            data={lineage.data}
            edge={selectedEdge}
            nodeUrn={selectedNodeUrn}
            collapsed={railCollapsed}
            onToggle={() => setRailCollapsed((value) => !value)}
            onRoot={reRoot}
            onSelectEdge={(edge) => {
              setSelectedEdgeKey(edge.edgeKey);
              setSelectedNodeUrn(null);
            }}
          />
        )}
      </div>
    </div>
  );
}
