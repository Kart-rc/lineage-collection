import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { StatusPill } from "../components/shared/StatusPill";


export function ReviewQueuePage() {
  const proposals = useQuery({
    queryKey: ["proposals"],
    queryFn: ({ signal }) => api.proposals(signal),
  });
  return (
    <div className="page list-page">
      <header className="page-header">
        <div><p className="eyebrow">Human gate</p><h1>Review queue</h1></div>
        <p>Confidence is a routing input. Evidence remains the decision record.</p>
      </header>
      {proposals.isPending ? <p className="empty-state">Loading routed proposals…</p> : null}
      {proposals.isError ? <p className="inline-error" role="alert">Review queue is unavailable.</p> : null}
      {proposals.data?.length === 0 ? <p className="empty-state">Nothing is waiting for review.</p> : null}
      <div className="record-list">
        {proposals.data?.map((proposal) => {
          const edge = proposal.diff.added[0];
          return (
            <Link key={`${proposal.proposalId}:${proposal.version}`} to={`/review/${proposal.proposalId}`}>
              <span className="record-list__index">{proposal.diff.added.length} edges</span>
              <div><strong>{proposal.system}</strong><code>{proposal.proposalId}</code></div>
              <StatusPill label={edge?.band ?? "NO BAND"} tone="trusted" />
              <span>{edge?.provenance.map((item) => item.mechanism).join(" + ")}</span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
