const STAGES = ["Signed push", "Analyze", "Consolidate", "Human gate", "Publish"];


export function FlowRail() {
  return (
    <ol className="flow-rail" aria-label="Push to publish flow">
      {STAGES.map((stage, index) => (
        <li key={stage}>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <strong>{stage}</strong>
        </li>
      ))}
    </ol>
  );
}
