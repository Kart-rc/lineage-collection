import type { RunStage } from "../../api/types";
import { shortDigest } from "../../lib/format";
import { stageTone } from "../../lib/runs";


export function StageTimeline({ stages }: { stages: RunStage[] }) {
  return (
    <ol className="stage-timeline">
      {stages.map((stage) => {
        const sha =
          typeof stage.detail.sha256 === "string"
            ? shortDigest(stage.detail.sha256, 14)
            : null;
        const key = typeof stage.detail.key === "string" ? stage.detail.key : null;
        return (
          <li key={stage.sequence} data-tone={stageTone(stage.status)}>
            <div className="stage-timeline__rail" aria-hidden="true">
              <span className="stage-timeline__dot" />
            </div>
            <div className="stage-timeline__body">
              <div className="stage-timeline__row">
                <h3>{stage.stage}</h3>
                <span className="stage-timeline__state">
                  {stage.status.replaceAll("_", " ").toLowerCase()}
                </span>
                {stage.completedAt && (
                  <span className="stage-timeline__when">
                    {new Date(stage.completedAt).toLocaleTimeString()}
                  </span>
                )}
              </div>
              <code className="stage-timeline__correlation">{stage.correlationId}</code>
              {Object.keys(stage.detail).length > 0 && (
                <details>
                  <summary>
                    Evidence{sha ? <code> · {sha}</code> : null}
                  </summary>
                  {key && <code className="stage-timeline__key">{key}</code>}
                  <pre>{JSON.stringify(stage.detail, null, 2)}</pre>
                </details>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
