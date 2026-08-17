import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "../routing";

import { ApiError, api } from "../api/client";
import type { LineageEdge, LineageResponse, Proposal } from "../api/types";
import { LineageCanvas } from "../components/lineage/LineageCanvas";
import { ProvenancePanel } from "../components/review/ProvenancePanel";
import { ReviewEdgeRow } from "../components/review/ReviewEdgeRow";
import { isRuntimeVerified } from "../components/review/reviewMeta";
import { readRuntimeConfig } from "../config/runtime";
import { useProposalEdges } from "../hooks/useProposalEdges";
import { shortDigest, statePillClass } from "../lib/format";
import "../styles/pages/review.css";

function shortSha(digest: string | undefined): string {
  return shortDigest(digest, 12, false) ?? "unavailable";
}

function titleFor(state: string): string {
  if (state === "IN_REVIEW") return "Awaiting your approval";
  if (state === "REJECTED") return "Rejected";
  return "Approved & published";
}

/** Build a renderable lineage view from the resolved proposal edges. */
function synthesizeLineage(proposal: Proposal, edges: LineageEdge[]): LineageResponse {
  const nodes = new Map<string, { urn: string; system: string; kind: "ELEMENT" | "DATASET" }>();
  for (const edge of edges) {
    for (const urn of [...edge.from, edge.to]) {
      if (!nodes.has(urn)) {
        nodes.set(urn, {
          urn,
          system: edge.system,
          kind: urn.includes("#") ? "ELEMENT" : "DATASET",
        });
      }
    }
  }
  return {
    subject: proposal.proposalId,
    direction: "both",
    namespaceVersion: proposal.expectedBaseVersion,
    depthSearched: 0,
    truncated: false,
    nodes: [...nodes.values()],
    edges,
  };
}

export function ProposalDetailPage() {
  const { proposalId = "" } = useParams();
  const proposal = useQuery({
    queryKey: ["proposals", proposalId],
    queryFn: ({ signal }) => api.proposal(proposalId, signal),
    enabled: Boolean(proposalId),
  });

  if (proposal.isPending) {
    return (
      <div className="page">
        <p className="empty-state">Loading proposal evidence…</p>
      </div>
    );
  }
  if (proposal.isError) {
    return (
      <div className="page">
        <p className="inline-error" role="alert">
          Proposal could not be loaded.
        </p>
      </div>
    );
  }
  return (
    <ProposalReview
      key={`${proposal.data.proposalId}:${proposal.data.version}`}
      proposal={proposal.data}
    />
  );
}

function ProposalReview({ proposal }: { proposal: Proposal }) {
  const runtime = readRuntimeConfig();
  const queryClient = useQueryClient();
  const [actor, setActor] = useState(
    runtime.demoActions ? "demo.reviewer@example.test" : "",
  );
  const [rationale, setRationale] = useState("");
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [selectedEdgeKey, setSelectedEdgeKey] = useState<string | null>(null);

  const addedIds = proposal.diff.addedEdgeIds;
  const removedIds = proposal.diff.removedEdgeIds;
  const bandChangedIds = proposal.diff.bandChangedEdgeIds;

  // The API hydrates diff edge bodies from the proposal's edge-set for
  // pre-publication review; the per-edge fan-out is the fallback for older
  // payloads whose diff carries only ids.
  const {
    edges,
    missing,
    edgesReady,
    isPending: edgesPending,
    isError: edgesError,
    total: allIdsTotal,
  } = useProposalEdges(proposal);

  const approve = useMutation({
    mutationFn: () =>
      api.approve(proposal.proposalId, {
        version: proposal.version,
        actor,
        rationale,
        expectedLockVersion: proposal.lockVersion,
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
      api.reject(proposal.proposalId, {
        version: proposal.version,
        actor,
        rationale,
        expectedLockVersion: proposal.lockVersion,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["proposals"] }),
  });

  const addedSet = useMemo(() => new Set(addedIds), [addedIds]);
  const removedSet = useMemo(() => new Set(removedIds), [removedIds]);
  const bandSet = useMemo(() => new Set(bandChangedIds), [bandChangedIds]);
  const addedEdges = edges.filter((edge) => addedSet.has(edge.edgeKey));
  const removedEdges = edges.filter((edge) => removedSet.has(edge.edgeKey));
  const bandEdges = edges.filter((edge) => bandSet.has(edge.edgeKey));
  const runtimeVerified = addedEdges.filter(isRuntimeVerified);
  const staticOnly = addedEdges.filter((edge) => !isRuntimeVerified(edge));

  const decisionMade = Boolean(approve.data) || Boolean(reject.data);
  const inReview = proposal.state === "IN_REVIEW" && !decisionMade;
  const checkedCount = staticOnly.filter((edge) => checked[edge.edgeKey]).length;
  const allChecked = staticOnly.length === 0 || checkedCount === staticOnly.length;
  const identityReady = actor.trim().length > 0 && rationale.trim().length > 0;
  const canApprove =
    inReview &&
    allChecked &&
    identityReady &&
    edgesReady &&
    missing === 0 &&
    !approve.isPending;
  const canReject = inReview && identityReady && !reject.isPending;
  const mutationError = approve.error ?? reject.error;

  const graph = useMemo(
    () => (addedEdges.length ? synthesizeLineage(proposal, addedEdges) : null),
    [proposal, addedEdges],
  );

  function toggle(edgeKey: string) {
    if (!inReview) return;
    setChecked((current) => ({ ...current, [edgeKey]: !current[edgeKey] }));
  }

  function focusEdgeRow(edge: LineageEdge) {
    setSelectedEdgeKey(edge.edgeKey);
    document
      .getElementById(`review-edge-${edge.edgeKey}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <div className="page detail-page proposal-page">
      <Link className="back-link" to="/review">
        ← Review queue
      </Link>
      <header className="detail-header">
        <div>
          <p className="eyebrow">
            {proposal.system} · proposal v{proposal.version}
          </p>
          <h1>{titleFor(proposal.state)}</h1>
        </div>
        <span className={statePillClass(proposal.state)}>{proposal.state}</span>
      </header>

      <dl className="proposal-facts" aria-label="Proposal facts">
        <div className="fact-chip">
          <dt>Repository</dt>
          <dd>{proposal.repository ?? "unknown"}</dd>
        </div>
        <div className="fact-chip">
          <dt>System</dt>
          <dd>{proposal.system}</dd>
        </div>
        <div className="fact-chip">
          <dt>Type</dt>
          <dd>{proposal.proposalType ?? "BASELINE"}</dd>
        </div>
        <div className="fact-chip">
          <dt>Command</dt>
          <dd>{proposal.commandId ?? proposal.correlationId}</dd>
        </div>
        <div className="fact-chip">
          <dt>Expected base</dt>
          <dd>{proposal.expectedBaseVersion}</dd>
        </div>
        <div className="fact-chip">
          <dt>Artifact digest</dt>
          <dd>{shortSha(proposal.artifactDigest)}</dd>
        </div>
        <div className="fact-chip">
          <dt>Environment</dt>
          <dd>{proposal.environment ?? "staging"}</dd>
        </div>
      </dl>

      {!edgesReady && edgesPending && allIdsTotal ? (
        <p className="empty-state">Resolving {allIdsTotal} edges from the ledger…</p>
      ) : null}
      {edgesError ? (
        <p className="inline-error" role="alert">
          Proposal edges could not be resolved from the ledger.
        </p>
      ) : null}
      {edgesReady && missing > 0 ? (
        <p className="inline-error" role="alert">
          {missing} of {allIdsTotal} edges could not be resolved — approval stays locked
          until every proposed edge is reviewable.
        </p>
      ) : null}

      {graph ? (
        <section className="proposal-graph-card" aria-labelledby="proposed-lineage-heading">
          <div className="proposal-graph-card__head">
            <p className="eyebrow" id="proposed-lineage-heading">
              Proposed lineage · {addedEdges.length} edges
            </p>
            <span className="proposal-graph-card__hint">
              Select an edge in the graph to jump to its review row
            </span>
          </div>
          <div className="proposal-graph-card__body">
            <LineageCanvas
              data={graph}
              selectedEdgeKey={selectedEdgeKey}
              onSelectEdge={focusEdgeRow}
              onSelectNode={() => {}}
            />
          </div>
        </section>
      ) : null}

      {edgesReady ? (
        <>
          <section className="review-queue" aria-labelledby="runtime-verified-heading">
            <div className="review-queue__head">
              <h2 id="runtime-verified-heading">
                Runtime-verified — corroborated by the execution harness
              </h2>
              <span className="review-queue__count">{runtimeVerified.length}</span>
            </div>
            <div className="review-queue__rows">
              {runtimeVerified.map((edge) => (
                <ReviewEdgeRow
                  key={edge.edgeKey}
                  edge={edge}
                  checked={null}
                  selected={selectedEdgeKey === edge.edgeKey}
                />
              ))}
              {!runtimeVerified.length ? (
                <p className="empty-state">No runtime-corroborated edges in this proposal.</p>
              ) : null}
            </div>
          </section>

          <section className="review-queue" aria-labelledby="static-only-heading">
            <div className="review-queue__head">
              <h2 id="static-only-heading">Needs your verification — static-only edges</h2>
              <span className="review-queue__count">{staticOnly.length}</span>
            </div>
            {staticOnly.length ? (
              <div className="review-progress">
                <span className="review-progress__label">
                  {checkedCount} / {staticOnly.length} checked
                </span>
                <span className="review-progress__track">
                  <span
                    className={`review-progress__fill${allChecked ? " review-progress__fill--complete" : ""}`}
                    style={{
                      width: `${staticOnly.length ? Math.round((checkedCount / staticOnly.length) * 100) : 100}%`,
                    }}
                  />
                </span>
              </div>
            ) : null}
            <div className="review-queue__rows">
              {staticOnly.map((edge) => (
                <ReviewEdgeRow
                  key={edge.edgeKey}
                  edge={edge}
                  checked={inReview ? Boolean(checked[edge.edgeKey]) : null}
                  onToggle={inReview ? toggle : undefined}
                  selected={selectedEdgeKey === edge.edgeKey}
                />
              ))}
              {!staticOnly.length ? (
                <p className="empty-state">
                  Every proposed edge was corroborated at runtime — nothing needs manual
                  verification.
                </p>
              ) : null}
            </div>
          </section>

          {removedEdges.length ? (
            <section className="review-queue" aria-labelledby="removed-heading">
              <div className="review-queue__head">
                <h2 id="removed-heading">Removed edges</h2>
                <span className="review-queue__count">{removedEdges.length}</span>
              </div>
              <div className="review-queue__rows">
                {removedEdges.map((edge) => (
                  <ReviewEdgeRow key={edge.edgeKey} edge={edge} checked={null} marker="removed" />
                ))}
              </div>
            </section>
          ) : null}

          {bandEdges.length ? (
            <section className="review-queue" aria-labelledby="band-changed-heading">
              <div className="review-queue__head">
                <h2 id="band-changed-heading">Confidence band changes</h2>
                <span className="review-queue__count">{bandEdges.length}</span>
              </div>
              <div className="review-queue__rows">
                {bandEdges.map((edge) => (
                  <ReviewEdgeRow key={edge.edgeKey} edge={edge} checked={null} marker="band" />
                ))}
              </div>
            </section>
          ) : null}

          {addedEdges.length ? (
            <ProvenancePanel provenance={addedEdges.flatMap((edge) => edge.provenance)} />
          ) : null}
        </>
      ) : null}

      <section className="decision-panel" aria-labelledby="decision-heading">
        <div>
          <p className="eyebrow">Human decision</p>
          <h2 id="decision-heading">
            {inReview ? "Record review outcome" : "Decision record"}
          </h2>
          {inReview ? (
            <p>The rationale is written to the immutable approval and audit records.</p>
          ) : null}
        </div>

        {inReview ? (
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
            {mutationError ? (
              <p className="inline-error" role="alert">
                {mutationError instanceof ApiError
                  ? mutationError.message
                  : "Decision could not be recorded."}
              </p>
            ) : null}
            <div className="decision-actions">
              <button
                className="button button--primary"
                type="button"
                disabled={!canApprove}
                title={
                  allChecked
                    ? undefined
                    : "Approve unlocks once every static-only edge is checked against its evidence"
                }
                onClick={() => approve.mutate()}
              >
                {approve.isPending ? "Recording approval…" : "Approve & publish"}
              </button>
              <button
                className="button button--secondary"
                type="button"
                disabled={!canReject}
                onClick={() => reject.mutate()}
              >
                {reject.isPending ? "Recording rejection…" : "Reject proposal"}
              </button>
            </div>
            {!allChecked && staticOnly.length ? (
              <p className="decision-gate-hint" id="approve-gate-hint">
                Approve unlocks once every static-only edge is checked against its evidence.
              </p>
            ) : null}
          </div>
        ) : null}

        {proposal.decision && !decisionMade ? (
          <div className="decision-record">
            <p>
              <strong>{proposal.decision.decision}</strong> by {proposal.decision.actor} ·{" "}
              {proposal.decision.decidedAt}
            </p>
            <p>“{proposal.decision.rationale}”</p>
          </div>
        ) : null}

        {approve.data ? (
          <div className="decision-receipt" role="status">
            <p className="decision-receipt__title">
              <span className="decision-receipt__dot" aria-hidden="true" />
              Approval recorded — publication is fenced and durable.
            </p>
            <dl>
              {approve.data.pointer ? (
                <div>
                  <dt>Active graph</dt>
                  <dd>{approve.data.pointer.activeVersion}</dd>
                </div>
              ) : null}
              {approve.data.publication ? (
                <div>
                  <dt>Publication</dt>
                  <dd>{approve.data.publication}</dd>
                </div>
              ) : null}
              <div>
                <dt>Approved by</dt>
                <dd>{actor}</dd>
              </div>
            </dl>
            {approve.data.run ? (
              <p>
                <Link to={`/runs/${approve.data.run.runId}`}>Track publication →</Link>
              </p>
            ) : null}
          </div>
        ) : null}

        {reject.data ? (
          <div className="decision-receipt decision-receipt--neutral" role="status">
            <p className="decision-receipt__title">
              Nothing was published — the active graph pointer is untouched.
            </p>
          </div>
        ) : null}
      </section>
    </div>
  );
}
