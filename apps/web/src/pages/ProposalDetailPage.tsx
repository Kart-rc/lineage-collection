import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "../routing";

import { ApiError, api } from "../api/client";
import { EdgeDiff } from "../components/review/EdgeDiff";
import { ProvenancePanel } from "../components/review/ProvenancePanel";
import { StatusPill } from "../components/shared/StatusPill";
import { readRuntimeConfig } from "../config/runtime";


export function ProposalDetailPage() {
  const { proposalId = "" } = useParams();
  const runtime = readRuntimeConfig();
  const [actor, setActor] = useState(
    runtime.demoActions ? "demo.reviewer@example.test" : "",
  );
  const [rationale, setRationale] = useState("");
  const queryClient = useQueryClient();
  const proposal = useQuery({
    queryKey: ["proposals", proposalId],
    queryFn: ({ signal }) => api.proposal(proposalId, signal),
    enabled: Boolean(proposalId),
  });
  const approve = useMutation({
    mutationFn: () =>
      api.approve(proposalId, {
        version: proposal.data!.version,
        actor,
        rationale,
        expectedLockVersion: proposal.data!.lockVersion,
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
        queryClient.invalidateQueries({ queryKey: ["proposals"] }),
        queryClient.invalidateQueries({ queryKey: ["runs"] }),
      ]);
    },
  });
  const reject = useMutation({
    mutationFn: () =>
      api.reject(proposalId, {
        version: proposal.data!.version,
        actor,
        rationale,
        expectedLockVersion: proposal.data!.lockVersion,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["proposals"] }),
  });

  if (proposal.isPending) return <div className="page"><p className="empty-state">Loading proposal evidence…</p></div>;
  if (proposal.isError) return <div className="page"><p className="inline-error" role="alert">Proposal could not be loaded.</p></div>;
  const allProvenance = proposal.data.diff.added.flatMap((item) => item.provenance);
  const mutationError = approve.error ?? reject.error;
  return (
    <div className="page detail-page proposal-page">
      <Link className="back-link" to="/review">← Review queue</Link>
      <header className="detail-header">
        <div><p className="eyebrow">{proposal.data.system} / proposal v{proposal.data.version}</p><h1>Proposal review</h1></div>
        <StatusPill label={proposal.data.state} tone="attention" />
      </header>
      <dl className="metadata-strip">
        <div><dt>Proposal</dt><dd><code>{proposal.data.proposalId}</code></dd></div>
        <div><dt>Expected base</dt><dd>{proposal.data.expectedBaseVersion}</dd></div>
        <div><dt>Correlation</dt><dd><code>{proposal.data.correlationId}</code></dd></div>
        <div><dt>Diff</dt><dd>{proposal.data.diff.addedEdgeIds.length} added</dd></div>
      </dl>
      <section aria-labelledby="edge-diff-heading">
        <div className="section-heading"><div><p className="eyebrow">Before / after</p><h2 id="edge-diff-heading">Proposed graph change</h2></div></div>
        <div className="edge-stack">
          {proposal.data.diff.added.map((item, index) => <EdgeDiff key={item.edgeKey} edge={item} index={index} />)}
          {!proposal.data.diff.added.length && (
            <div className="empty-state">
              <p>Canonical edge evidence is stored under the immutable edge-set reference.</p>
              <ul>
                {proposal.data.diff.addedEdgeIds.map((edgeId) => (
                  <li key={edgeId}><code>{edgeId}</code></li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </section>
      {allProvenance.length ? <ProvenancePanel provenance={allProvenance} /> : null}
      <section className="decision-panel" aria-labelledby="decision-heading">
        <div><p className="eyebrow">Human decision</p><h2 id="decision-heading">Record review outcome</h2><p>The rationale is written to the immutable approval and audit records.</p></div>
        <div className="decision-form">
          <label htmlFor="review-actor">Reviewer identity</label>
          <input
            id="review-actor"
            value={actor}
            onChange={(event) => setActor(event.target.value)}
            placeholder="name@example.com"
            autoComplete="username"
          />
          <label htmlFor="review-rationale">Review rationale</label>
          <textarea
            id="review-rationale"
            rows={4}
            value={rationale}
            onChange={(event) => setRationale(event.target.value)}
            placeholder="What evidence did you verify?"
          />
          {mutationError && (
            <p className="inline-error" role="alert">
              {mutationError instanceof ApiError ? mutationError.message : "Decision could not be recorded."}
            </p>
          )}
          {approve.data?.pointer && (
            <p className="success-message" role="status">
              Published into active graph {approve.data.pointer.activeVersion}
            </p>
          )}
          {approve.data?.publication && !approve.data.pointer && (
            <p className="success-message" role="status">
              Approval recorded. Publication is queued through the durable outbox.
            </p>
          )}
          {reject.data && <p className="success-message" role="status">Proposal rejected with an immutable decision record.</p>}
          <div className="decision-actions">
            <button className="button button--primary" type="button" disabled={!actor.trim() || !rationale.trim() || approve.isPending || Boolean(approve.data)} onClick={() => approve.mutate()}>
              {approve.isPending ? "Recording approval…" : "Approve for publication"}
            </button>
            <button className="button button--secondary" type="button" disabled={!actor.trim() || !rationale.trim() || reject.isPending || Boolean(approve.data)} onClick={() => reject.mutate()}>
              Reject proposal
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
