import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "../routing";

import { ApiError, api } from "../api/client";
import { FlowRail } from "../components/operations/FlowRail";
import { GateCard } from "../components/operations/GateCard";


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

      <FlowRail />

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
            />
            <GateCard
              index="02"
              label="Human review"
              value={`${data?.counts.inReview ?? 0} waiting`}
              detail="M1 deliberately keeps parser-exact edges behind review."
              tone={(data?.counts.inReview ?? 0) > 0 ? "attention" : "trusted"}
            />
            <GateCard
              index="03"
              label="Intake quarantine"
              value={`${data?.counts.quarantined ?? 0} held`}
              detail="Ambiguity and invalid identity never enter the ledger."
              tone={(data?.counts.quarantined ?? 0) > 0 ? "blocked" : "trusted"}
            />
            <GateCard
              index="04"
              label="Durable runs"
              value={`${data?.counts.runs ?? 0} recorded`}
              detail="Every stage shares one correlation identity."
            />
          </div>
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
