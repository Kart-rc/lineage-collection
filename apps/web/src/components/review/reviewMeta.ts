import type { ConfidenceBand, LineageEdge } from "../../api/types";

/** Display projection for a confidence band (mirrors product_confidence.py). */
export function bandDisplay(band: ConfidenceBand | string): {
  label: string;
  tone: "verified" | "probable";
} {
  switch (band) {
    case "HIGHEST":
      return { label: "VERIFIED 96", tone: "verified" };
    case "HIGH":
      return { label: "VERIFIED 92", tone: "verified" };
    case "MEDIUM":
      return { label: "PROBABLE 78", tone: "probable" };
    default:
      return { label: "PROBABLE 70", tone: "probable" };
  }
}

/** Last URN segment, with element suffix kept: owners#last_name. */
export function urnShort(urn: string): string {
  const [dataset, element] = urn.split("#");
  const tail = dataset.split(/[:/]/).filter(Boolean).pop() ?? dataset;
  return element ? `${tail}#${element}` : tail;
}

export function edgeLabel(edge: LineageEdge): string {
  const sources = edge.from.map(urnShort).join(", ");
  return `${sources} → ${urnShort(edge.to)}`;
}

/** An edge counts as runtime-verified when the harness corroborated it. */
export function isRuntimeVerified(edge: LineageEdge): boolean {
  return (
    edge.band === "HIGH" ||
    edge.band === "HIGHEST" ||
    edge.provenance.some((item) => item.mechanism === "RUNTIME")
  );
}

export function edgeCitation(edge: LineageEdge): string | null {
  const cited = edge.provenance.find((item) => item.citation);
  if (!cited?.citation) return null;
  const file = cited.citation.file.split("/").pop() ?? cited.citation.file;
  return `${file} · ${cited.citation.line}`;
}

export function edgeSignals(edge: LineageEdge): Array<"static" | "runtime" | "llm"> {
  const signals = new Set<"static" | "runtime" | "llm">();
  for (const item of edge.provenance) {
    if (item.mechanism === "SCA") signals.add("static");
    if (item.mechanism === "RUNTIME") signals.add("runtime");
    if (item.mechanism === "LLM") signals.add("llm");
  }
  return [...signals];
}
