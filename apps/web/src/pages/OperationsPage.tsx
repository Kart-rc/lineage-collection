import { useQuery } from "@tanstack/react-query";
import { Link } from "../routing";

import { api } from "../api/client";
import { DeploymentControl } from "../components/operations/DeploymentControl";
import { runStatusTone } from "../components/operations/StageRail";
import { StatusPill } from "../components/shared/StatusPill";
import type { OperationalStatus, Run } from "../api/types";
import "../styles/pages/operations.css";


type Tone = "trusted" | "attention" | "blocked" | "neutral";

const statusTone = (status: OperationalStatus): Tone => {
  if (["HEALTHY", "COMPLETE", "CURRENT", "IN_SYNC"].includes(status)) return "trusted";
  if (status === "OUT_OF_SYNC") return "blocked";
  if (["DEGRADED", "INCOMPLETE", "STALE"].includes(status)) return "attention";
  return "neutral";
};

const displayStatus = (status: OperationalStatus) =>
  status === "NOT_AVAILABLE" ? "Not available" : status.replaceAll("_", " ").toLowerCase();

const formatDuration = (seconds: number | null) => {
  if (seconds === null) return "no observation";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
};

const shortVersion = (version: string | null | undefined) =>
  version ? version.replace(/^graph-/, "").slice(0, 8) : "—";

const plural = (total: number, noun: string) =>
  `${total} ${noun}${total === 1 ? "" : "s"}`;

/** Whole seconds elapsed since an ISO timestamp, or null when unparseable. */
function secondsSince(now: number, iso: string | null | undefined): number | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  return Number.isFinite(then) ? Math.max(0, Math.round((now - then) / 1000)) : null;
}

function verdictFor(status: OperationalStatus | undefined): {
  title: string;
  tone: Tone;
  icon: string;
} {
  if (!status) return { title: "Reading plane health…", tone: "neutral", icon: "·" };
  const tone = statusTone(status);
  if (tone === "trusted") return { title: "The plane is healthy", tone, icon: "✓" };
  if (status === "OUT_OF_SYNC")
    return { title: "The plane is out of sync", tone: "blocked", icon: "✕" };
  if (tone === "attention")
    return { title: "The plane is degraded", tone, icon: "!" };
  return { title: "Plane health is not reported", tone: "neutral", icon: "·" };
}


interface AttentionItem {
  readonly key: string;
  readonly tone: Tone;
  readonly title: string;
  readonly meta: string;
  readonly cta?: { readonly label: string; readonly to: string };
}

interface CoverageRow {
  readonly system: string;
  readonly env: string;
  readonly baselineAgeSeconds: number | null;
  readonly lastRunSeconds: number | null;
  readonly latest: Run;
}

/** Group the run ledger by system — freshness of the latest published baseline. */
function coverageRows(runs: readonly Run[] | undefined, now: number): CoverageRow[] {
  const bySystem = new Map<string, Run[]>();
  for (const run of runs ?? []) {
    if (!run.system || /UNKNOWN/i.test(run.system)) continue;
    const list = bySystem.get(run.system) ?? [];
    list.push(run);
    bySystem.set(run.system, list);
  }
  return [...bySystem.entries()]
    .map(([system, list]) => {
      const sorted = list.slice().sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
      const latest = sorted[0];
      const baseline = sorted.find(
        (run) => run.workflowKind === "BASELINE" && run.state === "PUBLISHED",
      );
      return {
        system,
        env: latest.env,
        baselineAgeSeconds: baseline ? secondsSince(now, baseline.updatedAt) : null,
        lastRunSeconds: secondsSince(now, latest.updatedAt),
        latest,
      };
    })
    .sort((a, b) => (a.lastRunSeconds ?? Infinity) - (b.lastRunSeconds ?? Infinity));
}

const SPARK_HOURS = 24;

/** Runs started per hour over the trailing 24 h, oldest bucket first. */
function hourlyBuckets(runs: readonly Run[] | undefined, now: number): number[] {
  const buckets = Array.from({ length: SPARK_HOURS }, () => 0);
  for (const run of runs ?? []) {
    const started = Date.parse(run.createdAt);
    if (!Number.isFinite(started)) continue;
    const age = now - started;
    if (age < 0 || age >= SPARK_HOURS * 3_600_000) continue;
    buckets[SPARK_HOURS - 1 - Math.floor(age / 3_600_000)] += 1;
  }
  return buckets;
}


export function OperationsPage() {
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => api.overview(signal),
  });
  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: ({ signal }) => api.runs(signal),
  });
  const resilienceQuery = useQuery({
    queryKey: ["resilience"],
    queryFn: ({ signal }) => api.resilience(signal),
    enabled: !overview.data?.resilience,
  });

  const data = overview.data;
  const resilience = data?.resilience ?? resilienceQuery.data;
  const runs = runsQuery.data?.items;
  const now = Date.now();

  // Distinct systems from the ledger, most recently active first — the
  // deployment control promotes each system's latest approved package.
  const systems = [
    ...new Set(
      (runs ?? [])
        .slice()
        .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
        .map((run) => run.system)
        .filter((system) => system && !/UNKNOWN/i.test(system)),
    ),
  ];

  const signals = resilience
    ? [
        {
          label: "Queue",
          status: resilience.queue.status,
          value: String(resilience.queue.depth),
          unit: "pending",
          sub: `oldest ${formatDuration(resilience.queue.oldestAgeSeconds)} · ${resilience.queue.deadLetterCount} dead-letter`,
        },
        {
          label: "Correlation",
          status: resilience.correlation.status,
          value: String(resilience.correlation.trackedCount),
          unit: "tracked",
          sub: `${resilience.correlation.missingCount} missing — one identity per stage`,
        },
        {
          label: "Coverage",
          status: resilience.coverage.status,
          value:
            resilience.coverage.runtimeJoin.rate === null
              ? "—"
              : `${Math.round(resilience.coverage.runtimeJoin.rate * 100)}%`,
          unit: "runtime join",
          sub: `baseline age ${formatDuration(resilience.coverage.baseline.ageSeconds)} · ${resilience.coverage.incompleteCount} incomplete`,
        },
        {
          label: "Review latency",
          status: resilience.review.status,
          value:
            resilience.review.oldestApprovalAgeSeconds === null
              ? "—"
              : formatDuration(resilience.review.oldestApprovalAgeSeconds),
          unit: "oldest approval",
          sub: data
            ? `${data.counts.inReview} waiting at the human gate`
            : "the human gate ahead of every publication",
        },
        {
          label: "Publication",
          status: resilience.publication.status,
          value:
            resilience.publication.publishLagSeconds === null
              ? "—"
              : formatDuration(resilience.publication.publishLagSeconds),
          unit: "publish lag",
          sub: `pointer ${shortVersion(resilience.publication.pointerPackage.activeVersion)} · watermark ${shortVersion(resilience.publication.watermark.version)}`,
        },
        {
          label: "Control plane",
          status: resilience.status,
          value: displayStatus(resilience.status),
          unit: "",
          sub: resilience.capturedAt
            ? `captured ${new Date(resilience.capturedAt).toLocaleString()}`
            : "control-plane evidence only",
        },
      ]
    : [];
  const nominalCount = signals.filter(
    (signal) => statusTone(signal.status) === "trusted",
  ).length;

  const verdict = verdictFor(resilience?.status);
  const sublineParts: string[] = [];
  if (signals.length) {
    sublineParts.push(
      nominalCount === signals.length
        ? "All six signals nominal"
        : `${nominalCount} of ${signals.length} signals nominal`,
    );
  }
  if (data) {
    const oldestApproval = resilience?.review.oldestApprovalAgeSeconds ?? null;
    sublineParts.push(
      data.counts.inReview > 0
        ? `${data.counts.inReview} awaiting review${
            oldestApproval !== null
              ? ` — oldest has waited ${formatDuration(oldestApproval)}`
              : ""
          }`
        : "nothing waiting at the human gate",
    );
  }
  const subline =
    sublineParts.join(" · ") || "Reading durable control and projection state…";

  const refreshedSeconds =
    secondsSince(now, resilience?.capturedAt) ??
    (overview.dataUpdatedAt
      ? Math.max(0, Math.round((now - overview.dataUpdatedAt) / 1000))
      : null);

  // Triage queue — every item is derived from durable state, never invented.
  const attention: AttentionItem[] = [];
  if (data && data.counts.inReview > 0) {
    const sample = data.inReviewSample[0];
    const oldestApproval = resilience?.review.oldestApprovalAgeSeconds ?? null;
    attention.push({
      key: "review",
      tone: "attention",
      title: `${plural(data.counts.inReview, "proposal")} awaiting review`,
      meta:
        [
          sample ? `${sample.proposalId} · ${sample.system}` : null,
          oldestApproval !== null
            ? `oldest approval ${formatDuration(oldestApproval)}`
            : null,
        ]
          .filter(Boolean)
          .join(" · ") || "the human gate is holding publications",
      cta: { label: "Open queue", to: "/review" },
    });
  }
  if (resilience && resilience.queue.deadLetterCount > 0) {
    attention.push({
      key: "dead-letter",
      tone: "blocked",
      title: `${plural(resilience.queue.deadLetterCount, "message")} in the dead-letter queue`,
      meta: `queue depth ${resilience.queue.depth} · ${resilience.queue.retryCount} retries · ${resilience.queue.leaseStealCount} lease steals`,
    });
  }
  if (resilience) {
    const { status, ageSeconds, maxAgeSeconds } = resilience.coverage.baseline;
    if (
      status === "STALE" ||
      (ageSeconds !== null && maxAgeSeconds > 0 && ageSeconds > maxAgeSeconds)
    ) {
      attention.push({
        key: "baseline",
        tone: "attention",
        title: "Baseline is stale",
        meta: `age ${formatDuration(ageSeconds)} · policy max ${formatDuration(maxAgeSeconds)}`,
        cta: { label: "Re-baseline", to: "/onboard" },
      });
    }
  }
  for (const run of (runs ?? []).filter(
    (candidate) =>
      candidate.state === "FAILED" ||
      candidate.state === "QUARANTINED" ||
      candidate.failedStage !== null ||
      candidate.errorCode !== null,
  ).slice(0, 3)) {
    attention.push({
      key: `failed-${run.runId}`,
      tone: "blocked",
      title: `${run.workflowKind.replaceAll("_", " ").toLowerCase()} run ${run.state.replaceAll("_", " ").toLowerCase()}`,
      meta: `${run.runId} · ${run.system}${run.errorCode ? ` · ${run.errorCode}` : ""}`,
      cta: { label: "Open run", to: `/runs/${run.runId}` },
    });
  }
  for (const run of (runs ?? []).filter((candidate) => candidate.state === "BLOCK").slice(0, 2)) {
    attention.push({
      key: `block-${run.runId}`,
      tone: "blocked",
      title: "PR gate blocked a merge",
      meta: `${run.runId} · ${run.system}`,
      cta: { label: "Open run", to: `/runs/${run.runId}` },
    });
  }

  const buckets = hourlyBuckets(runs, now);
  const bucketTotal = buckets.reduce((sum, count) => sum + count, 0);
  const bucketMax = Math.max(...buckets, 1);

  const recentRuns = (runs ?? [])
    .slice()
    .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
    .slice(0, 6);

  const coverage = coverageRows(runs, now);
  const baselineMaxAge = resilience?.coverage.baseline.maxAgeSeconds ?? 0;

  return (
    <div className="page ops-page">
      <header className="ops-verdict" data-tone={verdict.tone}>
        <span className="ops-verdict__icon" aria-hidden="true">
          {verdict.icon}
        </span>
        <div className="ops-verdict__lines">
          <h1>{verdict.title}</h1>
          <p>{subline}</p>
        </div>
        <dl className="ops-verdict__facts">
          <div>
            <dt>Active graph</dt>
            <dd>
              {data
                ? `${data.activeVersion ?? "—"} · fence ${data.fencingToken}`
                : "—"}
            </dd>
          </div>
          <div>
            <dt>Refreshed</dt>
            <dd>
              {refreshedSeconds === null
                ? "—"
                : `${formatDuration(refreshedSeconds)} ago`}
            </dd>
          </div>
        </dl>
      </header>

      <section className="ops-signals" aria-labelledby="ops-signals-heading">
        <h2 id="ops-signals-heading" className="ops-sr">
          Operational signals
        </h2>
        {resilience ? (
          <div className="ops-signals__grid">
            {signals.map((signal) => (
              <article key={signal.label} className="ops-signal">
                <div className="ops-signal__top">
                  <span
                    className="ops-signal__dot"
                    data-tone={statusTone(signal.status)}
                    aria-hidden="true"
                  />
                  <span className="ops-signal__label">{signal.label}</span>
                  <span className="ops-signal__status">
                    {displayStatus(signal.status)}
                  </span>
                </div>
                <p className="ops-signal__value">
                  {signal.value}
                  {signal.unit && <span className="ops-signal__unit">{signal.unit}</span>}
                </p>
                <p className="ops-signal__sub">{signal.sub}</p>
              </article>
            ))}
          </div>
        ) : overview.isError || resilienceQuery.isError ? (
          <p className="inline-error" role="alert">
            Resilience signals are unavailable.
          </p>
        ) : (
          <p className="empty-state">Reading durable control and projection state…</p>
        )}
      </section>

      <div className="ops-band">
        <section
          className="ops-card ops-attention"
          aria-labelledby="ops-attention-heading"
        >
          <div className="ops-card__head">
            <h2 id="ops-attention-heading">Needs attention</h2>
            {attention.length > 0 && (
              <span className="ops-attention__count">{attention.length}</span>
            )}
          </div>
          {attention.length ? (
            <ul className="ops-attention__list">
              {attention.map((item) => (
                <li key={item.key} className="ops-attention__item">
                  <span
                    className="ops-attention__dot"
                    data-tone={item.tone}
                    aria-hidden="true"
                  />
                  <span className="ops-attention__body">
                    <strong>{item.title}</strong>
                    <span className="ops-attention__meta">{item.meta}</span>
                  </span>
                  {item.cta && (
                    <Link className="ops-attention__cta" to={item.cta.to}>
                      {item.cta.label}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="ops-attention__clear">
              Nothing needs a human right now — every derived triage source is clear.
            </p>
          )}
          <div className="ops-hourly">
            {bucketTotal > 0 ? (
              <div
                className="ops-hourly__bars"
                role="img"
                aria-label={`${plural(bucketTotal, "run")} started in the last 24 hours`}
              >
                {buckets.map((count, index) => (
                  <span
                    // Buckets are positional (one per trailing hour), so the
                    // index is the identity.
                    key={index}
                    className="ops-hourly__bar"
                    data-empty={count === 0 || undefined}
                    style={
                      count > 0
                        ? { height: `${Math.max(18, Math.round((count / bucketMax) * 100))}%` }
                        : undefined
                    }
                  />
                ))}
              </div>
            ) : (
              <p className="ops-hourly__empty">
                {runsQuery.isPending
                  ? "Reading the run ledger…"
                  : "No runs started in the last 24 h."}
              </p>
            )}
            <div className="ops-hourly__axis" aria-hidden="true">
              <span>runs / hour · 24 h</span>
              <span>now</span>
            </div>
          </div>
        </section>

        <section className="ops-card ops-ledger" aria-labelledby="ops-ledger-heading">
          <div className="ops-card__head">
            <h2 id="ops-ledger-heading">Recent runs</h2>
            <span className="ops-card__hint">execution ledger</span>
            <Link className="ops-card__link" to="/runs">
              All runs →
            </Link>
          </div>
          {runsQuery.isError ? (
            <p className="inline-error" role="alert">
              The run ledger is unavailable.
            </p>
          ) : recentRuns.length ? (
            <ul className="ops-ledger__list">
              {recentRuns.map((run) => (
                <li key={run.runId}>
                  <span className="workflow-chip" data-kind={run.workflowKind}>
                    {run.workflowKind.replaceAll("_", " ")}
                  </span>
                  <Link to={`/runs/${run.runId}`}>
                    <code>{run.runId}</code>
                  </Link>
                  <span className="ops-ledger__system">{run.system}</span>
                  <span
                    className="ops-ledger__status"
                    data-tone={runStatusTone(run.state)}
                  >
                    <span className="ops-ledger__statusdot" aria-hidden="true" />
                    {run.state.replaceAll("_", " ")}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty-state">
              {runsQuery.isPending
                ? "Reading the run ledger…"
                : "No durable run is available in this environment."}
            </p>
          )}
          {resilience && (
            <p className="ops-ledger__note">
              <span className="ops-ledger__notedot" aria-hidden="true" />
              <span>
                <strong>Every stage shares one correlation identity.</strong>{" "}
                {resilience.correlation.trackedCount} tracked ·{" "}
                {resilience.correlation.missingCount} missing — a break here would
                quarantine the run before derivation.
              </span>
            </p>
          )}
        </section>
      </div>

      <div className="ops-bottom">
        <section
          className="ops-card ops-card--framed ops-coverage"
          aria-labelledby="ops-coverage-heading"
        >
          <div className="ops-card__head ops-coverage__head">
            <h2 id="ops-coverage-heading">Coverage by system</h2>
            <span className="ops-card__hint">baseline freshness · run ledger</span>
            <Link className="ops-card__link" to="/onboard">
              Onboard a repository →
            </Link>
          </div>
          {runsQuery.isError ? (
            <p className="inline-error ops-coverage__message" role="alert">
              The run ledger is unavailable.
            </p>
          ) : coverage.length ? (
            <table className="ops-coverage__table">
              <thead>
                <tr>
                  <th scope="col">System</th>
                  <th scope="col">Env</th>
                  <th scope="col">Baseline age</th>
                  <th scope="col">Last run</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {coverage.map((row) => (
                  <tr key={row.system}>
                    <th scope="row">{row.system}</th>
                    <td className="ops-coverage__mono">{row.env}</td>
                    <td
                      className="ops-coverage__mono"
                      data-stale={
                        row.baselineAgeSeconds !== null &&
                        baselineMaxAge > 0 &&
                        row.baselineAgeSeconds > baselineMaxAge
                          ? true
                          : undefined
                      }
                    >
                      {row.baselineAgeSeconds === null
                        ? "—"
                        : formatDuration(row.baselineAgeSeconds)}
                    </td>
                    <td className="ops-coverage__mono">
                      {row.lastRunSeconds === null
                        ? "—"
                        : `${formatDuration(row.lastRunSeconds)} ago`}
                    </td>
                    <td>
                      <StatusPill
                        label={row.latest.state.replaceAll("_", " ")}
                        tone={runStatusTone(row.latest.state)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty-state ops-coverage__message">
              {runsQuery.isPending
                ? "Reading the run ledger…"
                : "No system has been collected yet — onboarding a repository starts the first baseline."}
            </p>
          )}
        </section>

        <section className="ops-card ops-deploy" aria-label="Deployment pipeline">
          <DeploymentControl
            systems={systems}
            environment={data?.environment ?? "staging"}
          />
        </section>
      </div>
    </div>
  );
}
