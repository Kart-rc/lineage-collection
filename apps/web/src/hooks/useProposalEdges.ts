import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api } from "../api/client";
import type { LineageEdge, Proposal } from "../api/types";

export interface ProposalEdgesResult {
  /** Resolved edge bodies: the proposal's hydrated diff arrays, or the /edges fan-out. */
  readonly edges: LineageEdge[];
  /** How many of the proposal's diff edge ids failed to resolve. Only meaningful once `edgesReady` is true. */
  readonly missing: number;
  /** True once the proposal payload itself carried every diff edge body — no fan-out needed. */
  readonly hydrationComplete: boolean;
  /** True once `edges` reflects a settled outcome (hydrated, or the fan-out query succeeded). */
  readonly edgesReady: boolean;
  /** The fan-out query's pending/error flags, for the "resolving…" / error messaging. */
  readonly isPending: boolean;
  readonly isError: boolean;
  /** Total diff edge ids (added + removed + bandChanged) — the denominator for "N of M". */
  readonly total: number;
}

/**
 * Resolve a proposal's diff edge ids to full edge bodies.
 *
 * The proposal payload hydrates diff edge bodies directly for pre-publication
 * review; older payloads carry only ids, so this falls back to a batched
 * `/edges/{key}` fan-out via `api.edges`. Both ProposalDetailPage and
 * RunComparePage used to copy-paste this exact id-concat + hydration +
 * missing-count logic; this hook is the single source of truth for it.
 *
 * Query key and staleTime are unchanged from both call sites:
 * `["proposal-edges", proposal?.proposalId, proposal?.version]`, staleTime Infinity —
 * diff edges are immutable once a proposal version exists, so a resolved
 * batch never needs to be refetched.
 */
export function useProposalEdges(proposal: Proposal | undefined): ProposalEdgesResult {
  const allIds = useMemo(
    () =>
      proposal
        ? [
            ...proposal.diff.addedEdgeIds,
            ...proposal.diff.removedEdgeIds,
            ...proposal.diff.bandChangedEdgeIds,
          ]
        : [],
    [proposal],
  );
  const hydrated = useMemo(
    () =>
      proposal
        ? [...proposal.diff.added, ...proposal.diff.removed, ...proposal.diff.bandChanged]
        : [],
    [proposal],
  );
  const hydrationComplete = allIds.length > 0 && hydrated.length >= allIds.length;

  const resolved = useQuery({
    queryKey: ["proposal-edges", proposal?.proposalId, proposal?.version],
    queryFn: ({ signal }) => api.edges(allIds, signal),
    enabled: Boolean(proposal) && allIds.length > 0 && !hydrationComplete,
    staleTime: Infinity,
  });

  const edges = hydrationComplete ? hydrated : resolved.data ?? [];
  const edgesReady = Boolean(proposal) && (hydrationComplete || resolved.isSuccess);
  const missing = allIds.length - edges.length;

  return {
    edges,
    missing,
    hydrationComplete,
    edgesReady,
    isPending: resolved.isPending,
    isError: resolved.isError,
    total: allIds.length,
  };
}
