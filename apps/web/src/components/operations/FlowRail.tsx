import type { ResilienceSnapshot } from "../../api/types";


interface FlowRailProps {
  snapshot?: ResilienceSnapshot;
}


const displayStatus = (status?: string) => status?.replaceAll("_", " ") ?? "LOADING";


export function FlowRail({ snapshot }: FlowRailProps) {
  const stages = [
    { label: "Signed push", status: snapshot?.queue.status },
    { label: "Analyze", status: snapshot?.coverage.status },
    { label: "Consolidate", status: snapshot?.coverage.runtimeJoin.status },
    { label: "Human gate", status: snapshot?.review.status },
    { label: "Publish", status: snapshot?.publication.status },
  ];
  return (
    <ol className="flow-rail" aria-label="Push to publish flow">
      {stages.map((stage, index) => (
        <li key={stage.label} data-status={stage.status ?? "LOADING"}>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <strong>{stage.label}</strong>
          <small>{displayStatus(stage.status)}</small>
        </li>
      ))}
    </ol>
  );
}
