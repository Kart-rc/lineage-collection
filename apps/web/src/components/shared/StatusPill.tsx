interface StatusPillProps {
  label: string;
  tone?: "neutral" | "trusted" | "attention" | "blocked";
  variant?: "tinted" | "solid" | "outline";
}


export function StatusPill({ label, tone = "neutral", variant = "tinted" }: StatusPillProps) {
  const variantClass = variant === "tinted" ? "" : ` status-pill--${variant}`;
  return (
    <span className={`status-pill status-pill--${tone}${variantClass}`}>
      <span className="status-pill__mark" aria-hidden="true" />
      {label}
    </span>
  );
}
