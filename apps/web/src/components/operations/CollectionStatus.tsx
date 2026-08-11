import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError, api } from "../../api/client";
import type { RepositoryCollection } from "../../api/types";
import { Link } from "../../routing";
import { StatusPill } from "../shared/StatusPill";


export const COLLECTION_POLL_INTERVAL_MS = 2_000;

export interface CollectionStatusProps {
  readonly commandId: string;
  /** The submit response, used as immediate data so the panel never flashes empty. */
  readonly initial?: RepositoryCollection;
  readonly onTerminal?: (collection: RepositoryCollection) => void;
}


export function collectionQueryOptions(commandId: string) {
  return {
    queryKey: ["collection", commandId] as const,
    queryFn: ({ signal }: { signal?: AbortSignal }) =>
      api.collection(commandId, signal),
    // Polling stops the moment the durable command reaches a terminal state.
    refetchInterval: (query: { state: { data?: RepositoryCollection } }) =>
      query.state.data?.terminal === false ? COLLECTION_POLL_INTERVAL_MS : false,
  };
}


function CountList({ collection }: { collection: RepositoryCollection }) {
  const counts = collection.counts;
  const coverage = collection.coverage;
  return (
    <dl className="collection-status__counts">
      <div><dt>Edges</dt><dd>{counts.edges}</dd></div>
      <div><dt>Reads</dt><dd>{counts.reads}</dd></div>
      <div><dt>Writes</dt><dd>{counts.writes}</dd></div>
      <div><dt>Residue</dt><dd>{counts.residue}</dd></div>
      <div><dt>Unresolved</dt><dd>{counts.unresolved}</dd></div>
      {coverage && (
        <>
          <div><dt>Coverage expected</dt><dd>{coverage.counts.expected}</dd></div>
          <div><dt>Coverage completed</dt><dd>{coverage.counts.completed}</dd></div>
          <div><dt>Coverage skipped</dt><dd>{coverage.counts.skipped}</dd></div>
          <div><dt>Coverage unsupported</dt><dd>{coverage.counts.unsupported}</dd></div>
          <div><dt>Coverage failed</dt><dd>{coverage.counts.failed}</dd></div>
        </>
      )}
    </dl>
  );
}


export function CollectionStatus({ commandId, initial, onTerminal }: CollectionStatusProps) {
  // A terminal command cannot change, so the seeded submit response is treated as
  // fresh forever. Without this the mount would issue one refetch of settled state.
  const settled = initial?.terminal === true;
  const status = useQuery({
    ...collectionQueryOptions(commandId),
    initialData: initial,
    initialDataUpdatedAt: initial ? Date.now() : undefined,
    staleTime: settled ? Infinity : 0,
  });

  const collection = status.data;
  const terminalCommandId = collection?.terminal ? collection.commandId : null;
  useEffect(() => {
    if (collection && terminalCommandId) onTerminal?.(collection);
    // Fires once per terminal command, not on every poll or re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [terminalCommandId]);

  if (status.isError) {
    const error = status.error;
    return (
      <section className="collection-status" aria-labelledby="collection-status-heading">
        <h3 id="collection-status-heading">Collection {commandId}</h3>
        <p className="inline-error" role="alert">
          {error instanceof ApiError
            ? `${error.message} (${error.code} · ${error.correlationId})`
            : "Collection status is unavailable."}
        </p>
      </section>
    );
  }

  if (!collection) {
    return (
      <section className="collection-status" aria-labelledby="collection-status-heading">
        <h3 id="collection-status-heading">Collection {commandId}</h3>
        <p className="empty-state">Reading durable collection state…</p>
      </section>
    );
  }

  return (
    <section className="collection-status" aria-labelledby="collection-status-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{collection.repository} · {collection.revision.slice(0, 12)}</p>
          <h3 id="collection-status-heading">Collection {collection.commandId}</h3>
        </div>
        <StatusPill
          label={collection.commandStatus}
          tone={collection.terminal ? "trusted" : "neutral"}
        />
      </div>

      <p role="status" aria-live="polite" className="collection-status__live">
        {collection.terminal
          ? `Collection ${collection.outcome} · run ${collection.runStatus ?? "unknown"}`
          : "Collection in progress…"}
      </p>

      {collection.stages.length > 0 && (
        <ol className="collection-status__stages">
          {collection.stages.map((stage, index) => (
            <li key={`${stage}-${index}`}>{stage}</li>
          ))}
        </ol>
      )}

      <CountList collection={collection} />

      <dl className="collection-status__meta">
        <div><dt>Runtime evidence</dt><dd>{collection.runtimeStatus}</dd></div>
        <div><dt>Analysis</dt><dd>{collection.analysisStatus ?? "—"}</dd></div>
        {collection.reasonCode && (
          <div><dt>Reason</dt><dd>{collection.reasonCode}</dd></div>
        )}
        {collection.statusReasons.length > 0 && (
          <div>
            <dt>Status reasons</dt>
            <dd>{collection.statusReasons.join(", ")}</dd>
          </div>
        )}
      </dl>

      <p className="collection-status__links">
        {collection.runId && (
          <Link to={`/runs/${collection.runId}`}>Open run timeline</Link>
        )}
        {collection.proposalId && (
          <Link to={`/review/${collection.proposalId}`}>Open proposal</Link>
        )}
      </p>
    </section>
  );
}
