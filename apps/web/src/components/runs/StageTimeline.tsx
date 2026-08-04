import type { RunStage } from "../../api/types";
import { StatusPill } from "../shared/StatusPill";


export function StageTimeline({ stages }: { stages: RunStage[] }) {
  return (
    <ol className="stage-timeline">
      {stages.map((stage) => (
        <li key={stage.sequence}>
          <div className="stage-timeline__rail" aria-hidden="true">
            <span>{String(stage.sequence).padStart(2, "0")}</span>
          </div>
          <div className="stage-timeline__body">
            <div>
              <h3>{stage.stage}</h3>
              <StatusPill label={stage.status} tone="trusted" />
            </div>
            <code>{stage.correlationId}</code>
            {Object.keys(stage.detail).length > 1 && (
              <details>
                <summary>Stage detail</summary>
                <pre>{JSON.stringify(stage.detail, null, 2)}</pre>
              </details>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
