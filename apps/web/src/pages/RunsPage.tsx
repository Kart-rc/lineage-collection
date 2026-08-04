import { useQuery } from "@tanstack/react-query";
import { Link } from "../routing";

import { api } from "../api/client";
import { StatusPill } from "../components/shared/StatusPill";


export function RunsPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: ({ signal }) => api.runs(signal) });
  return (
    <div className="page list-page">
      <header className="page-header">
        <div><p className="eyebrow">Execution ledger</p><h1>Runs</h1></div>
        <p>Durable stage histories for every accepted delivery.</p>
      </header>
      {runs.isPending ? <p className="empty-state">Loading run ledger…</p> : null}
      {runs.isError ? <p className="inline-error" role="alert">Run ledger is unavailable.</p> : null}
      {runs.data?.length === 0 ? <p className="empty-state">No runs yet. Start in Operations.</p> : null}
      <div className="record-list">
        {runs.data?.map((run) => (
          <Link key={run.runId} to={`/runs/${run.runId}`}>
            <span className="record-list__index">{run.stages.length} stages</span>
            <div><strong>{run.repo}</strong><code>{run.runId}</code></div>
            <StatusPill label={run.state} tone={run.state === "PUBLISHED" ? "trusted" : "attention"} />
            <code>{run.correlationId}</code>
          </Link>
        ))}
      </div>
    </div>
  );
}
