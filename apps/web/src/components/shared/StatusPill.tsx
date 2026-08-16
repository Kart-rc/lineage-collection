interface StatusPillProps {
  label: string;
  tone?: "neutral" | "trusted" | "attention" | "blocked";
}


export function StatusPill({ label, tone = "neutral" }: StatusPillProps) {
  return (
    <span className={`status-pill status-pill--${tone}`}>
      <span className="status-pill__mark" aria-hidden="true" />
      {label}
    </span>
  );
}
