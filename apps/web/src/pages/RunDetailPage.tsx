import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "../routing";

import { api } from "../api/client";
import { runStatusTone } from "../lib/runs";
import { StageTimeline } from "../components/runs/StageTimeline";
import { StatusPill } from "../components/shared/StatusPill";
import "../styles/pages/operations.css";


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
        <div>
          <p className="eyebrow">
            {run.data.workflowKind.replaceAll("_", " ")} · {run.data.repo}
          </p>
          <h1>Run timeline</h1>
        </div>
        <StatusPill
          label={run.data.state.replaceAll("_", " ")}
          tone={runStatusTone(run.data.state)}
        />
      </header>
      <dl className="metadata-strip">
        <div><dt>Run</dt><dd><code>{run.data.runId}</code></dd></div>
        <div><dt>Correlation</dt><dd><code>{run.data.correlationId}</code></dd></div>
        <div><dt>Environment</dt><dd>{run.data.env}</dd></div>
        {run.data.currentStage && (
          <div><dt>Stage</dt><dd><code>{run.data.currentStage}</code></dd></div>
        )}
        {run.data.updatedAt && (
          <div><dt>Updated</dt><dd>{new Date(run.data.updatedAt).toLocaleString()}</dd></div>
        )}
      </dl>
      <StageTimeline stages={run.data.stages} />
    </div>
  );
}
