import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "../routing";

import { api } from "../api/client";
import type { Proposal, Run, RunStage } from "../api/types";
import { formatDurationMs, shortDigest } from "../lib/format";
import {
  classifyRunState,
  formatWhen,
  matchProposal,
  runEdgeCount,
  runStatusTone,
  stagePipeline,
  stageTone,
  type ChipState,
} from "../lib/runs";
import "../styles/pages/runs.css";


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
