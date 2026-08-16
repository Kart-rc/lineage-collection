import type { Run } from "../../api/types";


const TERMINAL_STATUSES = new Set([
  "PUBLISHED",
  "PROMOTED",
  "BLOCK",
  "NO_LINEAGE_IMPACT",
  "FAILED",
  "QUARANTINED",
]);

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function runStatusTone(
  status: string,
): "trusted" | "attention" | "blocked" | "neutral" {
  if (status === "PUBLISHED" || status === "PROMOTED") return "trusted";
  if (status === "BLOCK" || status === "FAILED") return "blocked";
  if (status === "NO_LINEAGE_IMPACT") return "neutral";
  return "attention";
}

function stageNumber(stage: string): number {
  const match = /^[A-Z]+(\d+)$/.exec(stage);
  return match ? Number(match[1]) : 0;
}

export interface StageRailProps {
  readonly prefix: string;
  readonly total: number;
  /** The run driving the rail; absent renders every stage as pending. */
  readonly run?: Run;
  /** Stage id → caption rendered under that chip. */
  readonly annotations?: Readonly<Record<string, string>>;
}


export function StageRail({ prefix, total, run, annotations = {} }: StageRailProps) {
  const currentNumber = run ? stageNumber(run.currentStage) : 0;
  const terminal = run ? isTerminalStatus(run.state) : false;
  return (
    <ol
      className="stage-rail"
      aria-label={`${prefix} stages ${prefix}1 through ${prefix}${total}`}
    >
      {Array.from({ length: total }, (_, index) => {
        const stageId = `${prefix}${index + 1}`;
        const number = index + 1;
        const state = !run
          ? "pending"
          : number < currentNumber || (terminal && number <= currentNumber)
            ? "done"
            : number === currentNumber
              ? "current"
              : "pending";
        const caption = annotations[stageId];
        return (
          <li key={stageId} data-state={state}>
            <span className="stage-rail__chip">{stageId}</span>
            {caption && <span className="stage-rail__caption">{caption}</span>}
          </li>
        );
      })}
    </ol>
  );
}
