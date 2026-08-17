import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, usePathname } from "../routing";

import { api } from "../api/client";
import type { LineageEdge, Proposal, Run } from "../api/types";
import { bandDisplay, edgeLabel, isRuntimeVerified } from "../components/review/reviewMeta";
import { useProposalEdges } from "../hooks/useProposalEdges";
import { shortDigest } from "../lib/format";
import { classifyRunState, matchProposal, runEdgeCount, runStatusTone } from "../lib/runs";
import "../styles/pages/runs.css";


interface CompareParams {
  readonly candidate: string | null;
  readonly base: string | null;
}

/**
 * The router tracks only the pathname, so the query string is read from the
 * real location (window.location.search); a query embedded in a memory-router
 * pathname is honoured as a fallback for tests.
 */
function readCompareParams(pathname: string): CompareParams {
  const embedded = pathname.includes("?")
    ? pathname.slice(pathname.indexOf("?"))
    : window.location.search;
  const params = new URLSearchParams(embedded);
  return { candidate: params.get("candidate"), base: params.get("base") };
}

type ChangeKind = "added" | "removed" | "shift";
type EvidenceClass = "runtime" | "static" | "llm";

const CHANGE_TAGS: Record<ChangeKind, string> = {
  added: "ADDED",
  removed: "REMOVED",
  shift: "CONF ↑",
};

const EVIDENCE_LABELS: Record<EvidenceClass, string> = {
  runtime: "RUNTIME-VERIFIED",
  static: "STATIC-ONLY",
  llm: "LLM-ASSISTED",
};

/** Evidence class from what the edge actually carries — the canonical runtime-verified predicate. */
function evidenceClass(edge: LineageEdge): EvidenceClass {
  if (isRuntimeVerified(edge)) return "runtime";
  if (edge.provenance.some((item) => item.mechanism === "LLM")) return "llm";
  return "static";
}

function confidenceCell(kind: ChangeKind, edge: LineageEdge): string {
  const band = bandDisplay(edge.band).label;
  if (kind === "added") return `— → ${band}`;
  if (kind === "removed") return `${band} → —`;
  // The proposal payload records only the new band; the prior one isn't carried.
  return `shifted → ${band}`;
}

interface DiffRow {
  readonly kind: ChangeKind;
  readonly edge: LineageEdge;
}

function shortDate(iso: string): string | null {
  if (!iso) return null;
  const time = new Date(iso).getTime();
  if (!Number.isFinite(time)) return null;
  return new Date(time).toLocaleDateString(undefined, { month: "numeric", day: "numeric" });
}

function runMeta(run: Run, verb: string, when: string): string {
  const parts = [run.system, run.env];
  const digest = shortDigest(run.digest);
  if (digest) parts.push(`rev ${digest}`);
  const day = shortDate(when);
  if (day) parts.push(`${verb} ${day}`);
  const edges = runEdgeCount(run);
  if (edges !== null) parts.push(`${edges} edges`);
  return parts.filter(Boolean).join(" · ");
}


export function RunComparePage() {
  const pathname = usePathname();
  const [params, setParams] = useState<CompareParams>(() => readCompareParams(pathname));
  useEffect(() => {
    const sync = () => setParams(readCompareParams(window.location.pathname));
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  const [changeFilter, setChangeFilter] = useState<ChangeKind | "all">("all");
  const [evidenceFilter, setEvidenceFilter] = useState<EvidenceClass | "all">("all");

  const candidateId = params.candidate;
  // Note: a distinct key from the runs page's infinite query — same endpoint,
  // different cache shape.
  const ledger = useQuery({
    queryKey: ["runs", "compare-ledger"],
    queryFn: ({ signal }) => api.runs(signal),
  });
  const candidateDetail = useQuery({
    queryKey: ["runs", candidateId],
    queryFn: ({ signal }) => api.run(candidateId ?? "", signal),
    enabled: Boolean(candidateId),
  });
  const candidateLedger = ledger.data?.items.find((run) => run.runId === candidateId);
  const candidate = candidateDetail.data ?? candidateLedger;

  // Base: explicit ?base=, else the latest PUBLISHED baseline run of the same system.
  const baseId =
    params.base ??
    (candidate && ledger.data
      ? ledger.data.items
          .filter(
            (run) =>
              run.runId !== candidateId &&
              run.workflowKind === "BASELINE" &&
              classifyRunState(run.state) === "published" &&
              run.system === candidate.system,
          )
          .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))[0]?.runId ?? null
      : null);
  const baseDetail = useQuery({
    queryKey: ["runs", baseId],
    queryFn: ({ signal }) => api.run(baseId ?? "", signal),
    enabled: Boolean(baseId),
  });
  const base = baseDetail.data ?? ledger.data?.items.find((run) => run.runId === baseId);

  const proposals = useQuery({
    queryKey: ["proposals", "ledger"],
    queryFn: ({ signal }) => api.proposals(signal),
    enabled: Boolean(candidate),
  });
  const matched =
    candidate && proposals.data ? matchProposal(candidate, proposals.data.items) : null;
  const proposalDetail = useQuery({
    queryKey: ["proposals", matched?.proposalId],
    queryFn: ({ signal }) => api.proposal(matched?.proposalId ?? "", signal),
    enabled: Boolean(matched),
  });
  const proposal: Proposal | null = proposalDetail.data ?? matched;

  // Diff bodies: prefer the hydrated arrays on the proposal; fan out over
  // /edges/{key} only when the payload carries ids without bodies.
  const {
    edges,
    missing,
    edgesReady,
    isError: edgesError,
    total: allIdsTotal,
  } = useProposalEdges(proposal ?? undefined);

  const rows: DiffRow[] = useMemo(() => {
    if (!proposal) return [];
    const byKey = new Map(edges.map((edge) => [edge.edgeKey, edge]));
    const pick = (ids: readonly string[], kind: ChangeKind): DiffRow[] =>
      ids.flatMap((id) => {
        const edge = byKey.get(id);
        return edge ? [{ kind, edge }] : [];
      });
    return [
      ...pick(proposal.diff.addedEdgeIds, "added"),
      ...pick(proposal.diff.removedEdgeIds, "removed"),
      ...pick(proposal.diff.bandChangedEdgeIds, "shift"),
    ];
  }, [proposal, edges]);
  const visibleRows = rows.filter(
    (row) =>
      (changeFilter === "all" || row.kind === changeFilter) &&
      (evidenceFilter === "all" || evidenceClass(row.edge) === evidenceFilter),
  );
  const presentEvidence = [...new Set(rows.map((row) => evidenceClass(row.edge)))];

  const inReview = proposal?.state === "IN_REVIEW";
  const addedCount = proposal?.diff.addedEdgeIds.length ?? 0;

  function swapRuns() {
    if (!candidateId || !baseId) return;
    const next = new URLSearchParams({ candidate: baseId, base: candidateId });
    try {
      window.history.replaceState({}, "", `${window.location.pathname}?${next.toString()}`);
    } catch {
      // Memory-routed environments may not expose a mutable history; state still swaps.
    }
    setParams({ candidate: baseId, base: candidateId });
    setChangeFilter("all");
    setEvidenceFilter("all");
  }

  if (!candidateId) {
    return (
      <div className="page compare-page">
        <p className="compare-breadcrumb">
          <Link to="/runs">Runs</Link> / <strong>Compare</strong>
        </p>
        <p className="empty-state">
          No candidate run selected. Open a run in the{" "}
          <Link to="/runs">execution ledger</Link> and choose “Compare vs baseline”, or pass
          ?candidate=&lt;runId&gt;.
        </p>
      </div>
    );
  }

  const candidateMissing =
    candidateDetail.isError && ledger.isSuccess && !candidateLedger;

  return (
    <div className="page compare-page">
      <p className="compare-breadcrumb">
        <Link to="/runs">Runs</Link> / <strong>Compare</strong>
      </p>

      {candidateMissing ? (
        <p className="inline-error" role="alert">
          Run {candidateId} could not be found in the ledger.
        </p>
      ) : (
        <>
          <div className="compare-head">
            <div className="compare-tile">
              <span className="compare-tile__label">Base · run A</span>
              {base ? (
                <>
                  <span className="compare-tile__id">
                    <code>{base.runId}</code>
                    <span className="runs-kind" data-kind={base.workflowKind}>
                      {base.workflowKind.replaceAll("_", " ")}
                    </span>
                  </span>
                  <span className="compare-tile__meta">
                    {runMeta(
                      base,
                      classifyRunState(base.state) === "published" ? "published" : "updated",
                      base.updatedAt,
                    )}
                  </span>
                </>
              ) : ledger.isPending || (baseId && baseDetail.isPending) ? (
                <span className="compare-tile__meta">Resolving baseline…</span>
              ) : (
                <span className="compare-tile__meta">
                  No published baseline run found
                  {candidate ? ` for ${candidate.system}` : ""} — pass ?base=&lt;runId&gt;.
                </span>
              )}
            </div>
            <div className="compare-head__arrow" aria-hidden="true">
              →
            </div>
            <div className="compare-tile compare-tile--candidate">
              <span className="compare-tile__label">Candidate · run B</span>
              {candidate ? (
                <>
                  <span className="compare-tile__id">
                    <code>{candidate.runId}</code>
                    <span className="runs-kind" data-kind={candidate.workflowKind}>
                      {candidate.workflowKind.replaceAll("_", " ")}
                    </span>
                    <span
                      className="compare-tile__state"
                      data-tone={runStatusTone(candidate.state)}
                    >
                      {candidate.state.replaceAll("_", " ")}
                    </span>
                  </span>
                  <span className="compare-tile__meta">
                    {runMeta(
                      candidate,
                      proposal ? "proposed" : "updated",
                      proposal?.createdAt ?? candidate.updatedAt,
                    )}
                  </span>
                </>
              ) : candidateDetail.isPending || ledger.isPending ? (
                <span className="compare-tile__meta">Loading {candidateId}…</span>
              ) : (
                <span className="compare-tile__meta">Run {candidateId} not found.</span>
              )}
            </div>
            <div className="compare-head__swap">
              <button type="button" onClick={swapRuns} disabled={!baseId || !candidate}>
                swap runs ⇄
              </button>
            </div>
          </div>

          {proposal ? (
            <div className="compare-deltas">
              <button
                type="button"
                className="compare-delta"
                data-tone="added"
                aria-pressed={changeFilter === "added"}
                onClick={() =>
                  setChangeFilter(changeFilter === "added" ? "all" : "added")
                }
              >
                +{proposal.diff.addedEdgeIds.length} added
              </button>
              <button
                type="button"
                className="compare-delta"
                aria-pressed={changeFilter === "removed"}
                onClick={() =>
                  setChangeFilter(changeFilter === "removed" ? "all" : "removed")
                }
              >
                {proposal.diff.removedEdgeIds.length} removed
              </button>
              <button
                type="button"
                className="compare-delta"
                data-tone="shift"
                aria-pressed={changeFilter === "shift"}
                onClick={() =>
                  setChangeFilter(changeFilter === "shift" ? "all" : "shift")
                }
              >
                {proposal.diff.bandChangedEdgeIds.length} confidence shifts
              </button>
              {/* An "unchanged" count needs the base run's full edge total, which
                  the run records don't carry — so the chip is omitted, not faked. */}
              <span className="compare-deltas__spacer" />
              <select
                aria-label="Filter by evidence class"
                value={evidenceFilter}
                onChange={(event) =>
                  setEvidenceFilter(event.target.value as EvidenceClass | "all")
                }
              >
                <option value="all">evidence: all</option>
                {presentEvidence.map((cls) => (
                  <option key={cls} value={cls}>
                    {EVIDENCE_LABELS[cls].toLowerCase()}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="compare-deltas" />
          )}

          <div className="compare-table">
            <div className="compare-table__scroll">
              <div className="compare-grid compare-colhead" aria-hidden="true">
                <span>Change</span>
                <span>Edge</span>
                <span>Confidence A → B</span>
                <span>Evidence</span>
                <span />
              </div>

              {candidate && proposals.isSuccess && !matched ? (
                <p className="empty-state">
                  No proposal diff is attached to this run — a per-edge comparison isn’t
                  available.
                </p>
              ) : null}
              {proposals.isPending && candidate ? (
                <p className="empty-state">Looking up the run’s proposal…</p>
              ) : null}
              {proposal && !edgesReady && allIdsTotal > 0 ? (
                <p className="empty-state">
                  Resolving {allIdsTotal} diff edges from the ledger…
                </p>
              ) : null}
              {edgesError ? (
                <p className="inline-error" role="alert">
                  Diff edges could not be resolved from the ledger.
                </p>
              ) : null}
              {edgesReady && missing > 0 ? (
                <p className="inline-error" role="alert">
                  {missing} of {allIdsTotal} diff edges could not be resolved and are not
                  shown.
                </p>
              ) : null}
              {proposal && edgesReady && rows.length === 0 && missing === 0 ? (
                <p className="empty-state">The proposal diff is empty.</p>
              ) : null}
              {rows.length > 0 && visibleRows.length === 0 ? (
                <p className="empty-state">No diff rows match the current filters.</p>
              ) : null}

              {visibleRows.map((row) => {
                const cls = evidenceClass(row.edge);
                return (
                  <div
                    className="compare-grid compare-row"
                    key={`${row.kind}:${row.edge.edgeKey}`}
                  >
                    <span className="compare-tag" data-change={row.kind}>
                      {CHANGE_TAGS[row.kind]}
                    </span>
                    <span className="compare-row__edge" title={edgeLabel(row.edge)}>
                      {edgeLabel(row.edge)}
                    </span>
                    <span className="compare-row__conf">
                      {confidenceCell(row.kind, row.edge)}
                    </span>
                    <span className="compare-evidence" data-class={cls}>
                      {EVIDENCE_LABELS[cls]}
                    </span>
                    {proposal ? (
                      <Link
                        className="compare-row__link"
                        to={`/review/${proposal.proposalId}`}
                      >
                        Evidence →
                      </Link>
                    ) : (
                      <span />
                    )}
                  </div>
                );
              })}
            </div>

            {inReview && proposal ? (
              <div className="compare-footer" data-tone="gate">
                <span className="compare-footer__dot" aria-hidden="true" />
                <p>
                  Approving Run B publishes {addedCount === 1 ? "this" : "these"}{" "}
                  {addedCount} {addedCount === 1 ? "edge" : "edges"} via{" "}
                  <strong>fenced pointer swap</strong> — instant, atomic, reversible.
                  Approval is recorded on the review surface with rationale and an
                  optimistic lock.
                </p>
                <span className="compare-footer__spacer" />
                <Link
                  className="compare-footer__action"
                  to={`/review/${proposal.proposalId}`}
                >
                  Review &amp; reject
                </Link>
                <Link
                  className="compare-footer__action compare-footer__action--approve"
                  to={`/review/${proposal.proposalId}`}
                >
                  Review &amp; approve →
                </Link>
              </div>
            ) : (
              <div className="compare-footer">
                <span className="compare-footer__dot" aria-hidden="true" />
                <p>
                  {candidate && classifyRunState(candidate.state) === "published"
                    ? "Run B is already published — nothing awaits approval."
                    : "No proposal is awaiting review for this run — nothing to approve here."}
                </p>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
