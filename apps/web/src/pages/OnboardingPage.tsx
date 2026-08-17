import { useCallback, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "../routing";

import { ApiError, api } from "../api/client";
import { CollectionStatus } from "../components/operations/CollectionStatus";
import { RepositoryCollectionForm } from "../components/operations/RepositoryCollectionForm";
import type { RepositoryCollection, RepositoryCollectionRequest } from "../api/types";
import { readRuntimeConfig } from "../config/runtime";
import "../styles/pages/onboarding.css";


export function OnboardingPage() {
  const queryClient = useQueryClient();
  const runtime = readRuntimeConfig();
  const demoCollection = useMutation({
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
  const [submitted, setSubmitted] = useState<RepositoryCollection | null>(null);
  const [settled, setSettled] = useState<string | null>(null);
  const repositoryCollection = useMutation({
    mutationFn: (body: RepositoryCollectionRequest) => api.submitCollection(body),
    onSuccess: (result) => {
      setSubmitted(result);
      setSettled(null);
      queryClient.setQueryData(["collection", result.commandId], result);
    },
  });
  const onTerminal = useCallback(
    (finished: RepositoryCollection) => {
      // Invalidate once per finished collection, not on every poll.
      if (settled === finished.commandId) return;
      setSettled(finished.commandId);
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
        queryClient.invalidateQueries({ queryKey: ["runs"] }),
        queryClient.invalidateQueries({ queryKey: ["proposals"] }),
      ]);
    },
    [queryClient, settled],
  );

  return (
    <div className="page onboard-page">
      <h1 className="onboard-sr">Onboard a repository into lineage</h1>
      <RepositoryCollectionForm
        onSubmit={(body) => repositoryCollection.mutate(body)}
        pending={repositoryCollection.isPending}
        error={repositoryCollection.error}
        allowLocalCheckout={runtime.demoActions}
        trackingId={submitted?.commandId}
        tracking={
          submitted ? (
            <CollectionStatus
              commandId={submitted.commandId}
              initial={submitted}
              onTerminal={onTerminal}
            />
          ) : undefined
        }
      />
      {runtime.demoActions && (
        <section className="onboard-demo" aria-labelledby="demo-heading">
          <p className="onboard-demo__eyebrow" id="demo-heading">
            Local development only
          </p>
          <p>
            Resets local state and replays the seeded delivery — it does not
            collect a real repository.
          </p>
          <button
            className="button"
            type="button"
            onClick={() => demoCollection.mutate()}
            disabled={demoCollection.isPending}
          >
            {demoCollection.isPending
              ? "Collecting evidence…"
              : "Run seeded collection"}
          </button>
          {demoCollection.data?.run && (
            <div className="action-result" role="status">
              <strong>Delivery accepted into review</strong>
              <Link to={`/runs/${demoCollection.data.run.runId}`}>
                Open run timeline
              </Link>
            </div>
          )}
          {demoCollection.error && (
            <p className="inline-error" role="alert">
              {demoCollection.error instanceof ApiError
                ? demoCollection.error.message
                : "Collection could not be completed."}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
