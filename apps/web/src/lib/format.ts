/**
 * Small formatting helpers shared by pages/components that render run and
 * proposal identifiers, digests and durations. Before this consolidation
 * each of these was copy-pasted per call site with slightly different
 * parameters (length, ellipsis, prefix stripped); the canonical body is
 * kept here once and every call site passes the params it always used, so
 * rendered output is unchanged.
 */

/**
 * Shorten a `sha256:`-prefixed digest for display: strips the prefix,
 * truncates to `length` characters, and appends an ellipsis when truncated
 * (unless `ellipsis` is false). Returns null for a falsy value or one that
 * reads as "not reported" — callers that need a display fallback apply
 * `?? "..."` themselves, since the fallback text differs per call site.
 */
export function shortDigest(
  value: string | null | undefined,
  length = 12,
  ellipsis = true,
): string | null {
  if (!value || /not reported/i.test(value)) return null;
  const stripped = value.replace(/^sha256:/, "");
  if (stripped.length <= length) return stripped;
  return ellipsis ? `${stripped.slice(0, length)}…` : stripped.slice(0, length);
}

/**
 * Millisecond duration, tiered ms / s / m s / h m — the run ledger's stage
 * durations and human-gate waiting time.
 */
export function formatDurationMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1_000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
  return `${Math.floor(ms / 3_600_000)}h ${Math.floor((ms % 3_600_000) / 60_000)}m`;
}

/**
 * Second duration, tiered s / m s / h m — the operations plane's resilience
 * signals, which carry `number | null` ages ("no observation" when null).
 */
export function formatDurationSeconds(seconds: number | null): string {
  if (seconds === null) return "no observation";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

/**
 * Millisecond duration for the collection pipeline's step timings — a
 * compact ms/s-only rendering (no minutes/hours tier, a space before the
 * unit). Kept distinct from formatDurationMs because it renders
 * differently at the same input (e.g. "840 ms" vs "840ms"); merging them
 * would change CollectionStatus's on-screen output.
 */
export function formatDurationCompact(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

/** CSS class for a proposal/run state pill: `state-pill state-pill--<slug>`. */
export function statePillClass(state: string): string {
  return `state-pill state-pill--${state.toLowerCase().replace(/_/g, "-")}`;
}
