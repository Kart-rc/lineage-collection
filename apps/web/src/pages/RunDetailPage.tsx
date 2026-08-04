import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import { StageTimeline } from "../components/runs/StageTimeline";
import { StatusPill } from "../components/shared/StatusPill";


export function RunDetailPage() {
  const { runId = "" } = useParams();
  const run = useQuery({
    queryKey: ["runs", runId],
    queryFn: ({ signal }) => api.run(runId, signal),
    enabled: Boolean(runId),
  });
  if (run.isPending) return <div className="page"><p className="empty-state">Loading timeline…</p></div>;
  if (run.isError) return <div className="page"><p className="inline-error" role="alert">Run could not be loaded.</p></div>;
  return (
    <div className="page detail-page">
      <Link className="back-link" to="/runs">← All runs</Link>
      <header className="detail-header">
        <div><p className="eyebrow">{run.data.repo}</p><h1>Run timeline</h1></div>
        <StatusPill label={run.data.state} tone={run.data.state === "PUBLISHED" ? "trusted" : "attention"} />
      </header>
      <dl className="metadata-strip">
        <div><dt>Run</dt><dd><code>{run.data.runId}</code></dd></div>
        <div><dt>Correlation</dt><dd><code>{run.data.correlationId}</code></dd></div>
        <div><dt>Digest</dt><dd><code>{run.data.digest}</code></dd></div>
        <div><dt>Environment</dt><dd>{run.data.env}</dd></div>
      </dl>
      <StageTimeline stages={run.data.stages} />
    </div>
  );
}
