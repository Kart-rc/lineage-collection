import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "../routing";

import { api } from "../api/client";
import type { Proposal, Run, RunStage } from "../api/types";
import { runStatusTone } from "../components/operations/StageRail";
import "../styles/pages/runs.css";


/* ---------- run state classification (shared with the compare surface) ---------- */

export type RunCategory = "running" | "awaiting" | "failed" | "published" | "settled";

/** Bucket a durable run state into the ledger's triage categories. */
export function classifyRunState(state: string): RunCategory {
  const value = state.toUpperCase();
  if (value === "AWAITING_APPROVAL" || value === "IN_REVIEW") return "awaiting";
  if (
    value.includes("FAILED") ||
    value === "QUARANTINED" ||
    value === "ROLLED_BACK" ||
    value === "LINEAGE_OUT_OF_SYNC"
  ) {
    return "failed";
  }
  if (value === "PUBLISHED" || value === "PROMOTED") return "published";
  if (
    [
      "NO_LINEAGE_IMPACT",
      "NO_LINEAGE",
      "BLOCK",
      "PASS",
      "WARN",
      "REJECTED",
      "RECONCILED",
      "PROPOSALS_RAISED",
    ].includes(value)
  ) {
    return "settled";
  }
  return "running";
}

export function shortDigest(digest: string): string | null {
  if (!digest || /not reported/i.test(digest)) return null;
  const value = digest.replace(/^sha256:/, "");
  return value.length > 12 ? `${value.slice(0, 12)}…` : value;
}

/** Relative time for fresh entries, short local date otherwise. */
export function formatWhen(iso: string, now = Date.now()): string {
  if (!iso) return "—";
  const time = new Date(iso).getTime();
  if (!Number.isFinite(time)) return "—";
  const ageMs = now - time;
  if (ageMs < 60_000) return "just now";
  if (ageMs < 3_600_000) return `${Math.floor(ageMs / 60_000)}m ago`;
  if (ageMs < 172_800_000) return `${(ageMs / 3_600_000).toFixed(1)}h ago`;
  return new Date(time).toLocaleDateString();
}

export function formatDurationMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1_000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
  return `${Math.floor(ms / 3_600_000)}h ${Math.floor((ms % 3_600_000) / 60_000)}m`;
}

/**
 * The run's IN_REVIEW proposal, matched on the durable identifiers the two
 * records genuinely share: proposal.commandId ↔ run.runId, or the shared
 * correlation identity. Nothing else is matchable, so nothing else is used.
 */
export function matchProposal(run: Run, proposals: readonly Proposal[]): Proposal | null {
  const matches = proposals.filter(
    (proposal) =>
      proposal.commandId === run.runId ||
      (run.correlationId &&
        run.correlationId !== "unavailable" &&
        proposal.correlationId === run.correlationId),
  );
  if (!matches.length) return null;
  return matches.find((proposal) => proposal.state === "IN_REVIEW") ?? matches[0];
}

/** A real edge count only if the run's stage payloads carry one; never invented. */
export function runEdgeCount(run: Run): number | null {
  for (const stage of [...run.stages].reverse()) {
    const detail = stage.detail;
    for (const key of ["edges", "edgeCount", "proposedEdges"]) {
      const value = detail[key];
      if (typeof value === "number" && Number.isFinite(value) && value >= 0) return value;
    }
    const counts = detail.counts;
    if (counts && typeof counts === "object" && !Array.isArray(counts)) {
      const edges = (counts as Record<string, unknown>).edges;
      if (typeof edges === "number" && Number.isFinite(edges) && edges >= 0) return edges;
    }
  }
  return null;
}


/* ---------- six macro-phase pipeline (FE AN DE PR RV PB) ---------- */

type PhaseCode = "FE" | "AN" | "DE" | "PR" | "RV" | "PB";
type ChipState = "done" | "active" | "gate" | "failed" | "pending" | "na";

const PHASES: ReadonlyArray<{ code: PhaseCode; label: string }> = [
  { code: "FE", label: "fetch" },
  { code: "AN", label: "analyze" },
  { code: "DE", label: "derive" },
  { code: "PR", label: "propose" },
  { code: "RV", label: "review" },
  { code: "PB", label: "publish" },
];

type PhaseRange = readonly [number, number] | "gate" | null;

/**
 * Real stage ids mapped onto the six macro-phases. Baseline B1–B10 and
 * incremental I1–I10 share the same skeleton: B1–B4/I1–I4 acquire and plan
 * (FE), B5–B6/I5–I6 run static + runtime analysis (AN), B7–B8/I7–I8
 * consolidate (DE), B9/I9 create the proposal (PR), the human gate sits
 * between proposal and publish (RV), B10/I10 fence-and-publish (PB).
 * Deployment D1–D6 and PR gate P1–P8 have no proposal/review phases; those
 * chips render as honest "—".
 */
const PHASE_RANGES: Readonly<Record<string, Readonly<Record<PhaseCode, PhaseRange>>>> = {
  BASELINE: { FE: [1, 4], AN: [5, 6], DE: [7, 8], PR: [9, 9], RV: "gate", PB: [10, 10] },
  INCREMENTAL: { FE: [1, 4], AN: [5, 6], DE: [7, 8], PR: [9, 9], RV: "gate", PB: [10, 10] },
  DEPLOYMENT: { FE: [1, 2], AN: [3, 4], DE: null, PR: null, RV: null, PB: [5, 6] },
  PR_GATE: { FE: [1, 3], AN: [4, 5], DE: [6, 7], PR: null, RV: null, PB: [8, 8] },
};

function stageNumber(stage: string): number {
  const match = /^[A-Z]+(\d+)$/.exec(stage);
  return match ? Number(match[1]) : 0;
}

export interface PipelineChip {
  readonly code: PhaseCode;
  readonly label: string;
  readonly state: ChipState;
}

export function stagePipeline(run: Run): PipelineChip[] {
  const ranges = PHASE_RANGES[run.workflowKind];
  const category = classifyRunState(run.state);
  const current = stageNumber(run.currentStage);
  const failedAt = run.failedStage ? stageNumber(run.failedStage) : current;
  const proposalStage = ranges?.PR && ranges.PR !== "gate" ? ranges.PR[1] : 0;

  return PHASES.map(({ code, label }) => {
    const range = ranges?.[code] ?? null;
    if (!ranges || range === null) return { code, label, state: "na" as ChipState };

    if (range === "gate") {
      let state: ChipState = "pending";
      if (category === "awaiting") state = "gate";
      else if (category === "published") state = "done";
      else if (category === "settled") state = current >= proposalStage ? "done" : "pending";
      else if (category === "failed") state = failedAt > proposalStage ? "done" : "pending";
      else state = current > proposalStage ? "done" : "pending";
      return { code, label, state };
    }

    const [lo, hi] = range;
    let state: ChipState;
    if (category === "failed") {
      state = failedAt > hi ? "done" : failedAt >= lo ? "failed" : "pending";
    } else if (category === "awaiting") {
      state = hi <= current ? "done" : "pending";
    } else if (category === "published" || category === "settled") {
      state = lo <= current ? "done" : "pending";
    } else {
      state = current > hi ? "done" : current >= lo ? "active" : "pending";
    }
    return { code, label, state };
  });
}

function chipStateLabel(state: ChipState): string {
  if (state === "na") return "not applicable";
  if (state === "gate") return "waiting at the human gate";
  return state;
}

export function StagePipeline({ run }: { run: Run }) {
  const chips = stagePipeline(run);
  return (
    <ol className="runs-pipeline" aria-label={`Stage pipeline for ${run.runId}`}>
      {chips.map((chip) => (
        <li
          key={chip.code}
          data-state={chip.state}
          aria-label={`${chip.code} ${chip.label}: ${chipStateLabel(chip.state)}`}
        >
          {chip.state === "na" ? "—" : chip.code}
        </li>
      ))}
    </ol>
  );
}


/* ---------- expanded in-place triage ---------- */

interface ArtifactRef {
  readonly key: string;
  readonly stage: string;
  readonly checksum: string | null;
}

/** Checksummed artifact refs the stage payloads genuinely carry (key + sha256). */
function stageArtifacts(stages: readonly RunStage[]): ArtifactRef[] {
  const artifacts: ArtifactRef[] = [];
  for (const stage of stages) {
    const key = stage.detail.key;
    if (typeof key !== "string" || !key) continue;
    const sha = stage.detail.sha256;
    artifacts.push({
      key,
      stage: stage.stage,
      checksum: typeof sha === "string" && sha ? sha : null,
    });
  }
  return artifacts;
}

function artifactExt(key: string): string {
  const tail = key.split("/").pop() ?? key;
  const dot = tail.lastIndexOf(".");
  if (dot > 0 && tail.length - dot <= 6) return tail.slice(dot + 1);
  return "ref";
}

function stageTone(status: string): "done" | "active" | "failed" | "pending" {
  if (status === "COMPLETED" || status === "SUCCEEDED") return "done";
  if (status === "RUNNING" || status === "IN_PROGRESS" || status === "STARTED") return "active";
  if (status === "FAILED" || status === "ERROR") return "failed";
  return "pending";
}

function StageBars({ stages }: { stages: readonly RunStage[] }) {
  const windows = stages
    .map((stage) => ({
      start: new Date(stage.startedAt).getTime(),
      end: new Date(stage.completedAt).getTime(),
    }))
    .map((w) => (Number.isFinite(w.start) && Number.isFinite(w.end) ? w : null));
  const valid = windows.filter((w): w is { start: number; end: number } => w !== null);
  const min = valid.length ? Math.min(...valid.map((w) => w.start)) : 0;
  const max = valid.length ? Math.max(...valid.map((w) => w.end)) : 0;
  const span = Math.max(max - min, 1);

  return (
    <ol className="runs-stagelist" aria-label="Stage timeline">
      {stages.map((stage, index) => {
        const w = windows[index];
        const duration = w ? w.end - w.start : null;
        return (
          <li key={stage.stage} data-tone={stageTone(stage.status)}>
            <span className="runs-stagelist__name">
              <i aria-hidden="true" />
              <span>{stage.stage}</span>
            </span>
            <span className="runs-stagelist__track" aria-hidden="true">
              {w ? (
                <span
                  className="runs-stagelist__fill"
                  style={{
                    left: `${((w.start - min) / span) * 100}%`,
                    width: `${Math.max(((w.end - w.start) / span) * 100, 1.5)}%`,
                  }}
                />
              ) : null}
            </span>
            <span className="runs-stagelist__dur">
              {duration !== null ? formatDurationMs(duration) : "—"}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function RunTriage({ run, panelId }: { run: Run; panelId: string }) {
  const detail = useQuery({
    queryKey: ["runs", run.runId],
    queryFn: ({ signal }) => api.run(run.runId, signal),
  });
  const proposals = useQuery({
    queryKey: ["proposals", "ledger"],
    queryFn: ({ signal }) => api.proposals(signal),
  });

  const proposal = proposals.data ? matchProposal(run, proposals.data.items) : null;
  const inReview = proposal?.state === "IN_REVIEW";
  const stages = detail.data?.stages ?? [];
  const totalMs = stages.reduce((sum, stage) => {
    const start = new Date(stage.startedAt).getTime();
    const end = new Date(stage.completedAt).getTime();
    return Number.isFinite(start) && Number.isFinite(end) ? sum + (end - start) : sum;
  }, 0);
  const artifacts = stageArtifacts(stages);
  const waiting = proposal?.createdAt
    ? Date.now() - new Date(proposal.createdAt).getTime()
    : null;

  return (
    <div className="runs-triage" id={panelId}>
      <div className="runs-triage__banner" data-tone={inReview ? "gate" : undefined}>
        <span className="runs-triage__dot" aria-hidden="true" />
        {inReview && proposal ? (
          <>
            <strong>
              Waiting at the human gate — {proposal.proposalId} ready for review
            </strong>
            <small>
              +{proposal.diff.addedEdgeIds.length} −{proposal.diff.removedEdgeIds.length} ~
              {proposal.diff.bandChangedEdgeIds.length} edges
              {waiting !== null && waiting >= 0
                ? ` · waiting ${formatDurationMs(waiting)}`
                : ""}
            </small>
          </>
        ) : (
          <strong>
            {run.state.replaceAll("_", " ")}
            {run.currentStage ? ` · stage ${run.currentStage}` : ""}
          </strong>
        )}
        <span className="runs-triage__spacer" />
        {inReview && proposal ? (
          <Link
            className="runs-triage__action runs-triage__action--primary"
            to={`/review/${proposal.proposalId}`}
          >
            Review proposal
          </Link>
        ) : null}
        <Link className="runs-triage__action" to={`/runs/compare?candidate=${run.runId}`}>
          Compare vs baseline
        </Link>
        <Link className="runs-triage__action" to={`/runs/${run.runId}`}>
          Full run detail →
        </Link>
      </div>
      <div className="runs-triage__grid">
        <div className="runs-triage__timeline">
          <span className="runs-triage__label">
            Stage timeline
            {stages.length && totalMs > 0 ? ` · ${formatDurationMs(totalMs)} total` : ""}
          </span>
          {detail.isPending ? (
            <p className="runs-triage__note">Loading stage history…</p>
          ) : detail.isError ? (
            <p className="runs-triage__note" role="alert">
              Stage history could not be loaded.
            </p>
          ) : stages.length ? (
            <StageBars stages={stages} />
          ) : (
            <p className="runs-triage__note">No durable stage history on this run.</p>
          )}
        </div>
        <div className="runs-triage__artifacts">
          <span className="runs-triage__label">Artifacts · checksummed</span>
          {detail.isPending ? (
            <p className="runs-triage__note">Loading artifact refs…</p>
          ) : artifacts.length ? (
            artifacts.map((artifact) => (
              <div className="runs-artifact" key={`${artifact.stage}:${artifact.key}`}>
                <span className="runs-artifact__ext" aria-hidden="true">
                  {artifactExt(artifact.key)}
                </span>
                <span className="runs-artifact__body">
                  <code>{artifact.key}</code>
                  <small>
                    {artifact.stage}
                    {artifact.checksum ? ` · ${artifact.checksum.slice(0, 12)}…` : ""}
                  </small>
                </span>
              </div>
            ))
          ) : (
            <p className="runs-triage__note">No artifact manifest on this run.</p>
          )}
        </div>
      </div>
    </div>
  );
}


/* ---------- the ledger page ---------- */

type StatusFilter = "all" | "running" | "awaiting" | "failed" | "published";

const STATUS_FILTERS: ReadonlyArray<{ id: StatusFilter; label: string; tone?: string }> = [
  { id: "all", label: "All" },
  { id: "running", label: "Running", tone: "running" },
  { id: "awaiting", label: "Awaiting approval", tone: "awaiting" },
  { id: "failed", label: "Failed", tone: "failed" },
  { id: "published", label: "Published" },
];

export function RunsPage() {
  const runs = useInfiniteQuery({
    queryKey: ["runs", "ledger"],
    queryFn: ({ signal, pageParam }) => api.runs(signal, pageParam || undefined),
    initialPageParam: "",
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
  });
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [systemFilter, setSystemFilter] = useState("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const items = useMemo(
    () => runs.data?.pages.flatMap((page) => page.items) ?? [],
    [runs.data],
  );
  const counts = useMemo(() => {
    const tally: Record<StatusFilter, number> = {
      all: items.length,
      running: 0,
      awaiting: 0,
      failed: 0,
      published: 0,
    };
    for (const run of items) {
      const category = classifyRunState(run.state);
      if (category !== "settled") tally[category] += 1;
    }
    return tally;
  }, [items]);
  const systems = useMemo(
    () =>
      [...new Set(items.map((run) => run.system).filter((s) => s && !/UNKNOWN/i.test(s)))],
    [items],
  );
  const visible = items.filter(
    (run) =>
      (statusFilter === "all" || classifyRunState(run.state) === statusFilter) &&
      (systemFilter === "all" || run.system === systemFilter),
  );

  return (
    <div className="page runs-page">
      <div className="runs-ledger">
        <header className="runs-header">
          <div className="runs-header__title">
            <h1>Runs</h1>
            <p>execution ledger · durable stage histories</p>
          </div>
          {STATUS_FILTERS.map((filter) => (
            <button
              key={filter.id}
              type="button"
              className="runs-filter"
              data-tone={filter.tone}
              aria-pressed={statusFilter === filter.id}
              onClick={() => setStatusFilter(filter.id)}
            >
              {filter.label} · {counts[filter.id]}
            </button>
          ))}
          <span className="runs-header__spacer" />
          <select
            aria-label="Filter by system"
            value={systemFilter}
            onChange={(event) => setSystemFilter(event.target.value)}
          >
            <option value="all">system: all</option>
            {systems.map((system) => (
              <option key={system} value={system}>
                {system}
              </option>
            ))}
          </select>
        </header>

        <div className="runs-scroll">
          <div className="runs-grid runs-colhead" aria-hidden="true">
            <span>Type</span>
            <span>Command</span>
            <span>System · revision</span>
            <span>Stages — FE AN DE PR RV PB</span>
            <span>Edges</span>
            <span>Status</span>
          </div>

          {runs.isPending ? <p className="runs-empty">Loading run ledger…</p> : null}
          {runs.isError ? (
            <p className="runs-inline-error" role="alert">
              Run ledger is unavailable.
            </p>
          ) : null}
          {runs.data && items.length === 0 ? (
            <p className="runs-empty">No runs have been recorded.</p>
          ) : null}
          {runs.data && items.length > 0 && visible.length === 0 ? (
            <p className="runs-empty">No runs match the current filters.</p>
          ) : null}

          <div className="runs-rows">
            {visible.map((run) => {
              const expanded = expandedId === run.runId;
              const panelId = `run-triage-${run.runId}`;
              const digest = shortDigest(run.digest);
              const edgeCount = runEdgeCount(run);
              return (
                <div className="runs-row" key={run.runId}>
                  <button
                    type="button"
                    className="runs-grid runs-row__main"
                    aria-expanded={expanded}
                    aria-controls={expanded ? panelId : undefined}
                    onClick={() => setExpandedId(expanded ? null : run.runId)}
                  >
                    <span className="runs-kind" data-kind={run.workflowKind}>
                      {run.workflowKind.replaceAll("_", " ")}
                    </span>
                    <span className="runs-row__cmd">
                      <code>{run.runId}</code>
                      <small>{formatWhen(run.updatedAt || run.createdAt)}</small>
                    </span>
                    <span className="runs-row__system">
                      <strong>{run.system}</strong>
                      <code>{digest ? `rev ${digest}` : "revision not reported"}</code>
                    </span>
                    <StagePipeline run={run} />
                    <span className="runs-row__edges">
                      {edgeCount !== null ? edgeCount : "—"}
                    </span>
                    <span className="runs-status" data-tone={runStatusTone(run.state)}>
                      <i aria-hidden="true" />
                      <span>{run.state.replaceAll("_", " ")}</span>
                    </span>
                  </button>
                  {expanded ? <RunTriage run={run} panelId={panelId} /> : null}
                </div>
              );
            })}
          </div>
        </div>

        <footer className="runs-legend">
          <span className="runs-legend__key">
            <i data-state="done" aria-hidden="true" />done
          </span>
          <span className="runs-legend__key">
            <i data-state="active" aria-hidden="true" />active
          </span>
          <span className="runs-legend__key">
            <i data-state="gate" aria-hidden="true" />gate
          </span>
          <span className="runs-legend__key">
            <i data-state="failed" aria-hidden="true" />failed
          </span>
          <span className="runs-legend__phases">
            · FE fetch · AN analyze · DE derive · PR propose · RV review · PB publish
          </span>
          <span className="runs-legend__count">
            {visible.length
              ? `1–${visible.length} of ${items.length}${runs.hasNextPage ? "+" : ""}`
              : `0 of ${items.length}${runs.hasNextPage ? "+" : ""}`}
            {runs.hasNextPage ? (
              <button
                type="button"
                className="runs-legend__more"
                onClick={() => runs.fetchNextPage()}
                disabled={runs.isFetchingNextPage}
              >
                {runs.isFetchingNextPage ? "Loading…" : "Load more"}
              </button>
            ) : null}
          </span>
        </footer>
      </div>
    </div>
  );
}
