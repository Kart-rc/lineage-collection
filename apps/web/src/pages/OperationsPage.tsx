import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "../routing";

import { ApiError, api } from "../api/client";
import { FlowRail } from "../components/operations/FlowRail";
import { GateCard } from "../components/operations/GateCard";
import type { OperationalStatus } from "../api/types";


const displayStatus = (status: OperationalStatus) => status.replaceAll("_", " ");

const statusTone = (status: OperationalStatus) => {
  if (["HEALTHY", "COMPLETE", "CURRENT", "IN_SYNC"].includes(status)) return "trusted" as const;
  if (status === "OUT_OF_SYNC") return "blocked" as const;
  if (["DEGRADED", "INCOMPLETE", "STALE"].includes(status)) return "attention" as const;
  return "neutral" as const;
};

const formatDuration = (seconds: number | null) => {
  if (seconds === null) return "No observation";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
};


export function OperationsPage() {
  const queryClient = useQueryClient();
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => api.overview(signal),
  });
  const collection = useMutation({
    mutationFn: async () => {
      const demo = await api.reset();
      return api.collect(demo.demoDelivery);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
        queryClient.invalidateQueries({ queryKey: ["runs"] }),
        queryClient.invalidateQueries({ queryKey: ["proposals"] }),
      ]);
    },
  });

  const data = overview.data;
  const resilience = data?.resilience;
  const signals = resilience
    ? [
        { label: "Oldest queue age", value: formatDuration(resilience.queue.oldestAgeSeconds), status: resilience.queue.status },
        { label: "Queue saturation", value: `${resilience.queue.depth} observed / capacity unset`, status: resilience.queue.saturation.status },
        { label: "Retries / DLQ", value: `${resilience.queue.retryCount} retries / ${resilience.queue.deadLetterCount} dead-letter`, status: resilience.queue.status },
        { label: "Lease steals", value: String(resilience.queue.leaseStealCount), status: resilience.queue.status },
        { label: "Incomplete coverage", value: String(resilience.coverage.incompleteCount), status: resilience.coverage.status },
        { label: "Runtime join rate", value: resilience.coverage.runtimeJoin.rate === null ? "No eligible runs" : `${Math.round(resilience.coverage.runtimeJoin.rate * 100)}%`, status: resilience.coverage.runtimeJoin.status },
        { label: "Baseline freshness", value: formatDuration(resilience.coverage.baseline.ageSeconds), status: resilience.coverage.baseline.status },
        { label: "Approval age", value: formatDuration(resilience.review.oldestApprovalAgeSeconds), status: resilience.review.status },
        { label: "Publish lag", value: formatDuration(resilience.publication.publishLagSeconds), status: resilience.publication.status },
        { label: "Pointer / package", value: `${resilience.publication.pointerPackage.activeVersion ?? "—"} / ${resilience.publication.pointerPackage.packageVersion ?? "—"}`, status: resilience.publication.pointerPackage.status },
        { label: "Projection watermark", value: `${resilience.publication.watermark.version ?? "—"} · ${formatDuration(resilience.publication.watermark.ageSeconds)}`, status: resilience.publication.watermark.status },
        { label: "Replication", value: "Production evidence unavailable", status: resilience.productionSignals.replication.status },
        { label: "Error-budget burn", value: "SLO window unavailable", status: resilience.productionSignals.errorBudgetBurn.status },
        { label: "Unit cost", value: "Billing adapter unavailable", status: resilience.productionSignals.unitCost.status },
      ]
    : [];
  return (
    <div className="page operations-page">
      <section className="operations-hero">
        <div>
          <p className="eyebrow">Evidence control plane / staging</p>
          <h1>From signal to trusted graph.</h1>
          <p className="lede">
            A deterministic, inspectable path from one signed repository push to a
            versioned lineage projection.
          </p>
        </div>
        <aside className="launch-card">
          <span className="launch-card__label">Recommended demo</span>
          <h2>Collect the payments pipeline</h2>
          <p>Reset local state, verify a signed push, and stop at the human review gate.</p>
          <button
            className="button button--primary"
            type="button"
            onClick={() => collection.mutate()}
            disabled={collection.isPending}
          >
            {collection.isPending ? "Collecting evidence…" : "Run seeded collection"}
          </button>
          {collection.data?.run && (
            <div className="action-result" role="status">
              <strong>Delivery accepted into review</strong>
              <Link to={`/runs/${collection.data.run.runId}`}>Open run timeline</Link>
            </div>
          )}
          {collection.error && (
            <p className="inline-error" role="alert">
              {collection.error instanceof ApiError
                ? collection.error.message
                : "Collection could not be completed."}
            </p>
          )}
        </aside>
      </section>

      <FlowRail snapshot={resilience} />

      <section className="gate-section" aria-labelledby="gate-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Current launch gates</p>
            <h2 id="gate-heading">Operational posture</h2>
          </div>
          <span>Pointer fence {data?.fencingToken ?? "—"}</span>
        </div>
        {overview.isError ? (
          <p className="inline-error" role="alert">Operational posture is unavailable.</p>
        ) : (
          <div className="gate-grid">
            <GateCard
              index="01"
              label="Active projection"
              value={data?.activeVersion ?? "…"}
              detail="Queries resolve through this immutable namespace."
              tone="trusted"
              statusLabel="IN SYNC"
            />
            <GateCard
              index="02"
              label="Human review"
              value={`${data?.counts.inReview ?? 0} waiting`}
              detail="M1 deliberately keeps parser-exact edges behind review."
              tone={(data?.counts.inReview ?? 0) > 0 ? "attention" : "trusted"}
              statusLabel={(data?.counts.inReview ?? 0) > 0 ? "ATTENTION" : "CLEAR"}
            />
            <GateCard
              index="03"
              label="Intake quarantine"
              value={`${data?.counts.quarantined ?? 0} held`}
              detail="Ambiguity and invalid identity never enter the ledger."
              tone={(data?.counts.quarantined ?? 0) > 0 ? "blocked" : "trusted"}
              statusLabel={(data?.counts.quarantined ?? 0) > 0 ? "BLOCKED" : "CLEAR"}
            />
            <GateCard
              index="04"
              label="Durable runs"
              value={`${data?.counts.runs ?? 0} recorded`}
              detail="Every stage shares one correlation identity."
              statusLabel={resilience ? displayStatus(resilience.correlation.status) : "LOADING"}
              tone={resilience ? statusTone(resilience.correlation.status) : "neutral"}
            />
          </div>
        )}
      </section>

      <section className="resilience-section" aria-labelledby="resilience-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Runtime and resilience</p>
            <h2 id="resilience-heading">Operational snapshot</h2>
          </div>
          <span>
            {resilience
              ? `${displayStatus(resilience.status)} · ${new Date(resilience.capturedAt).toLocaleString()}`
              : "Loading durable signals…"}
          </span>
        </div>
        {resilience ? (
          <>
            <div className="resilience-gates">
              <GateCard
                index="Q"
                label="Queue health"
                value={`${resilience.queue.depth} pending`}
                detail="Retry, dead-letter, lease recovery, age and observed depth."
                statusLabel={displayStatus(resilience.queue.status)}
                tone={statusTone(resilience.queue.status)}
              />
              <GateCard
                index="C"
                label="Evidence coverage"
                value={`${resilience.coverage.incompleteCount} incomplete`}
                detail="Coverage and optional exact-artifact runtime joins stay separate."
                statusLabel={displayStatus(resilience.coverage.status)}
                tone={statusTone(resilience.coverage.status)}
              />
              <GateCard
                index="P"
                label="Publication truth"
                value={resilience.publication.pointerPackage.activeVersion
                  ? `Graph ${resilience.publication.pointerPackage.activeVersion}`
                  : "No pointer"}
                detail="Active pointer, approved package and projection watermark must agree."
                statusLabel={displayStatus(resilience.publication.status)}
                tone={statusTone(resilience.publication.status)}
              />
              <GateCard
                index="X"
                label="Production proof"
                value="Unclaimed"
                detail="Replication, error-budget and cost need deployed telemetry."
                statusLabel="NOT CONFIGURED"
                tone="neutral"
              />
            </div>
            <dl className="signal-ledger">
              {signals.map((signal) => (
                <div key={signal.label}>
                  <dt>{signal.label}</dt>
                  <dd>
                    <strong>{signal.value}</strong>
                    <span data-tone={statusTone(signal.status)}>{displayStatus(signal.status)}</span>
                  </dd>
                </div>
              ))}
            </dl>
          </>
        ) : overview.isError ? (
          <p className="inline-error" role="alert">Resilience signals are unavailable.</p>
        ) : (
          <p className="empty-state">Reading durable control and projection state…</p>
        )}
      </section>

      <section className="recent-section" aria-labelledby="recent-heading">
        <div className="section-heading">
          <div><p className="eyebrow">Execution ledger</p><h2 id="recent-heading">Recent runs</h2></div>
          <Link to="/runs">View all runs</Link>
        </div>
        {!data?.recentRuns.length ? (
          <p className="empty-state">No run has been collected in this local state.</p>
        ) : (
          <div className="table-list">
            {data.recentRuns.map((run) => (
              <Link key={run.runId} to={`/runs/${run.runId}`}>
                <strong>{run.repo}</strong><span>{run.state}</span><code>{run.correlationId}</code>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
