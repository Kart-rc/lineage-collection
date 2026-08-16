import type { Run } from "../../api/types";
import { stageNumber } from "../../lib/runs";

// Re-exported so existing consumers of this module keep working unchanged.
export { runStatusTone } from "../../lib/runs";


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
