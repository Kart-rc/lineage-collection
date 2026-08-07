import type {
  ApiErrorShape,
  CollectionResult,
  DemoReset,
  ImpactResponse,
  LineageEdge,
  LineageResponse,
  Overview,
  Proposal,
  ResilienceSnapshot,
  Run,
} from "./types";


export class ApiError extends Error {
  readonly code: string;
  readonly correlationId: string;
  readonly details?: Record<string, unknown>;

  constructor(payload: ApiErrorShape) {
    super(payload.message);
    this.name = "ApiError";
    this.code = payload.code;
    this.correlationId = payload.correlationId;
    this.details = payload.details;
  }
}


async function request<T>(
  path: string,
  options: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    signal,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const payload = (await response.json()) as T | ApiErrorShape;
  if (!response.ok) {
    throw new ApiError(payload as ApiErrorShape);
  }
  return payload as T;
}


export const api = {
  overview: (signal?: AbortSignal) => request<Overview>("/api/overview", {}, signal),
  resilience: (signal?: AbortSignal) =>
    request<ResilienceSnapshot>("/api/operations/resilience", {}, signal),
  reset: () => request<DemoReset>("/api/demo/reset", { method: "POST" }),
  collect: (demo: DemoReset["demoDelivery"]) =>
    request<CollectionResult>("/api/events/push", {
      method: "POST",
      body: JSON.stringify(demo),
    }),
  runs: (signal?: AbortSignal) => request<Run[]>("/api/runs", {}, signal),
  run: (runId: string, signal?: AbortSignal) =>
    request<Run>(`/api/runs/${encodeURIComponent(runId)}`, {}, signal),
  proposals: (signal?: AbortSignal) =>
    request<Proposal[]>("/api/proposals", {}, signal),
  proposal: (proposalId: string, signal?: AbortSignal) =>
    request<Proposal>(`/api/proposals/${encodeURIComponent(proposalId)}`, {}, signal),
  approve: (
    proposalId: string,
    input: {
      version: number;
      actor: string;
      rationale: string;
      expectedLockVersion: number;
    },
  ) =>
    request<{ proposal: Proposal; run: Run; pointer: { activeVersion: string } }>(
      `/api/proposals/${encodeURIComponent(proposalId)}/approve`,
      { method: "POST", body: JSON.stringify(input) },
    ),
  reject: (
    proposalId: string,
    input: {
      version: number;
      actor: string;
      rationale: string;
      expectedLockVersion: number;
    },
  ) =>
    request<{ proposal: Proposal; run: Run }>(
      `/api/proposals/${encodeURIComponent(proposalId)}/reject`,
      { method: "POST", body: JSON.stringify(input) },
    ),
  lineage: (
    urn: string,
    direction: "up" | "down",
    depth: number,
    signal?: AbortSignal,
  ) =>
    request<LineageResponse>(
      `/api/lineage/${encodeURIComponent(urn)}?direction=${direction}&depth=${depth}`,
      {},
      signal,
    ),
  edge: (edgeKey: string, signal?: AbortSignal) =>
    request<LineageEdge>(`/api/edges/${encodeURIComponent(edgeKey)}`, {}, signal),
  impact: (subject: string, changeType: string, depth: number) =>
    request<ImpactResponse>("/api/impact", {
      method: "POST",
      body: JSON.stringify({ subject, changeType, depth }),
    }),
};
