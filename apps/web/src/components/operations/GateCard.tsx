import { StatusPill } from "../shared/StatusPill";


interface GateCardProps {
  index: string;
  label: string;
  value: string;
  detail: string;
  tone?: "neutral" | "trusted" | "attention" | "blocked";
  statusLabel?: string;
}


export function GateCard({
  index,
  label,
  value,
  detail,
  tone = "neutral",
  statusLabel,
}: GateCardProps) {
  return (
    <article className="gate-card">
      <div className="gate-card__topline">
        <span className="gate-card__index">{index}</span>
        <StatusPill label={label} tone={tone} />
      </div>
      <strong className="gate-card__value">{value}</strong>
      {statusLabel ? <span className={`gate-card__state gate-card__state--${tone}`}>{statusLabel}</span> : null}
      <p>{detail}</p>
    </article>
  );
}
