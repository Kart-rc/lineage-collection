import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "../routing";

import { api } from "../api/client";
import type { Proposal } from "../api/types";
import { statePillClass } from "../lib/format";
import "../styles/pages/review.css";

const STATES = ["IN_REVIEW", "APPROVED", "REJECTED", "FINALIZED"] as const;
type ProposalState = (typeof STATES)[number];

const STATE_LABELS: Record<ProposalState, string> = {
  IN_REVIEW: "In review",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  FINALIZED: "Finalized",
};

const EMPTY_COPY: Record<ProposalState, string> = {
  IN_REVIEW: "Nothing in review — the queue is clear.",
  APPROVED: "No approved proposals in this window.",
  REJECTED: "No rejected proposals in this window.",
  FINALIZED: "No finalized proposals in this window.",
};

function readableTime(iso: string): string {
  if (!iso) return "time unavailable";
  const stamp = new Date(iso);
  return Number.isNaN(stamp.getTime()) ? iso : stamp.toLocaleString();
}

function ProposalCard({ proposal }: { proposal: Proposal }) {
  const added = proposal.diff.addedEdgeIds.length;
  const removed = proposal.diff.removedEdgeIds.length;
  const bandChanged = proposal.diff.bandChangedEdgeIds.length;
  const type = proposal.proposalType ?? "BASELINE";
  return (
    <Link className="proposal-card" to={`/review/${proposal.proposalId}`}>
      <span className="proposal-card__top">
        <span
          className={`type-chip type-chip--${type === "DELTA" ? "delta" : "baseline"}`}
        >
          {type}
        </span>
        <span className="proposal-card__id" title={proposal.proposalId}>
          {proposal.proposalId.slice(0, 24)}…
        </span>
        <span className={statePillClass(proposal.state)}>{proposal.state}</span>
      </span>
      <span className="proposal-card__meta">
        <strong>
          {proposal.system}
          {proposal.repository ? ` · ${proposal.repository}` : ""}
        </strong>
        <span>
          +{added} −{removed} ~{bandChanged} edges
        </span>
      </span>
      <span className="proposal-card__when">
        <span>{readableTime(proposal.updatedAt)}</span>
        {proposal.decision ? (
          <span>
            {proposal.decision.decision.toLowerCase()} by {proposal.decision.actor}
          </span>
        ) : null}
      </span>
    </Link>
  );
}

export function ReviewQueuePage() {
  const [state, setState] = useState<ProposalState>("IN_REVIEW");
  const proposals = useQuery({
    queryKey: ["proposals", state],
    queryFn: ({ signal }) => api.proposals(signal, undefined, state),
  });
  return (
    <div className="page list-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Review</p>
          <h1>Proposal review</h1>
        </div>
        <p className="lede">The human gate — no edge is published without passing it.</p>
      </header>
      <div className="review-filters" role="group" aria-label="Filter proposals by state">
        {STATES.map((option) => (
          <button
            key={option}
            type="button"
            className="review-filter"
            aria-pressed={state === option}
            onClick={() => setState(option)}
          >
            {STATE_LABELS[option]}
            {state === option && proposals.data ? (
              <span className="review-filter__count">{proposals.data.items.length}</span>
            ) : null}
          </button>
        ))}
      </div>
      {proposals.isPending ? <p className="empty-state">Loading proposals…</p> : null}
      {proposals.isError ? (
        <p className="inline-error" role="alert">
          Review queue is unavailable.
        </p>
      ) : null}
      {proposals.data?.items.length === 0 ? (
        <p className="empty-state">{EMPTY_COPY[state]}</p>
      ) : null}
      <div className="proposal-cards">
        {proposals.data?.items.map((proposal) => (
          <ProposalCard
            key={`${proposal.proposalId}:${proposal.version}`}
            proposal={proposal}
          />
        ))}
      </div>
      {proposals.data?.nextCursor ? (
        <p className="empty-state">More proposals are available through bounded pagination.</p>
      ) : null}
    </div>
  );
}
