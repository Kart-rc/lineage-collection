/**
 * The run-state vocabulary and six-macro-phase pipeline engine — moved out
 * of RunsPage.tsx (which used to own it) so RunComparePage, StageRail and
 * StageTimeline can consume the same durable-state classification without
 * importing a page module.
 */
import type { Proposal, Run } from "../api/types";


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
export type ChipState = "done" | "active" | "gate" | "failed" | "pending" | "na";

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
export const PHASE_RANGES: Readonly<Record<string, Readonly<Record<PhaseCode, PhaseRange>>>> = {
  BASELINE: { FE: [1, 4], AN: [5, 6], DE: [7, 8], PR: [9, 9], RV: "gate", PB: [10, 10] },
  INCREMENTAL: { FE: [1, 4], AN: [5, 6], DE: [7, 8], PR: [9, 9], RV: "gate", PB: [10, 10] },
  DEPLOYMENT: { FE: [1, 2], AN: [3, 4], DE: null, PR: null, RV: null, PB: [5, 6] },
  PR_GATE: { FE: [1, 3], AN: [4, 5], DE: [6, 7], PR: null, RV: null, PB: [8, 8] },
};

/** Numeric suffix of a stage id like "B7" or "I10" → 7 / 10; 0 if unparseable. */
export function stageNumber(stage: string): number {
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


/* ---------- stage / run status tone ---------- */

/** Tone for a single stage's execution status (stage timelines/bars). */
export function stageTone(status: string): "done" | "active" | "failed" | "pending" {
  if (status === "COMPLETED" || status === "SUCCEEDED") return "done";
  if (status === "RUNNING" || status === "IN_PROGRESS" || status === "STARTED") return "active";
  if (status === "FAILED" || status === "ERROR") return "failed";
  return "pending";
}

/** Tone for a run's overall durable state (ledger rows, status pills, StageRail). */
export function runStatusTone(
  status: string,
): "trusted" | "attention" | "blocked" | "neutral" {
  if (status === "PUBLISHED" || status === "PROMOTED") return "trusted";
  if (status === "BLOCK" || status === "FAILED") return "blocked";
  if (status === "NO_LINEAGE_IMPACT") return "neutral";
  return "attention";
}
