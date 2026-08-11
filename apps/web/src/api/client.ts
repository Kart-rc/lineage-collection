import { apiPath, readRuntimeConfig } from "../config/runtime";
import type {
  ApiErrorShape,
  CollectionCounts,
  CollectionCoverage,
  CollectionResult,
  RepositoryCollection,
  RepositoryCollectionRequest,
  DemoReset,
  ImpactResponse,
  LineageEdge,
  LineageResponse,
  Overview,
  Page,
  Proposal,
  ResilienceSnapshot,
  Run,
  RunStage,
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

export interface ReviewResult {
  readonly proposal: Proposal;
  readonly publication?: string;
  readonly approvalRef?: Record<string, unknown>;
  readonly run?: Run;
  readonly pointer?: { readonly activeVersion: string };
}


function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}


function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}


function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record) : [];
}


function apiError(value: unknown, status: number): ApiErrorShape {
  const payload = record(value);
  const nested = record(payload.error);
  const code = text(nested.code || payload.code, `HTTP_${status || 500}`);
  const message = text(
    nested.message || payload.message,
    "The lineage service could not complete the request.",
  );
  const correlationId = text(payload.correlationId, "unavailable");
  const details = record(nested.details || payload.details);
  return {
    code,
    message,
    correlationId,
    ...(Object.keys(details).length ? { details } : {}),
  };
}


async function request(
  path: string,
  options: RequestInit = {},
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetch(apiPath(path), {
    ...options,
    signal,
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError({
      code: "INVALID_RESPONSE",
      message: "The lineage service returned an invalid response.",
      correlationId: "unavailable",
    });
  }
  if (!response.ok) throw new ApiError(apiError(payload, response.status));
  return payload;
}


function normalizeStage(
  value: unknown,
  index: number,
  correlationId: string,
): RunStage {
  const stage = record(value);
  const output = record(stage.output || stage.detail);
  return {
    sequence:
      typeof stage.sequence === "number" && Number.isInteger(stage.sequence)
        ? stage.sequence
        : index + 1,
    stage: text(stage.stageName || stage.stage || stage.stageId, "UNKNOWN"),
    status: text(stage.status, "UNKNOWN"),
    correlationId: text(stage.correlationId, correlationId),
    detail: output,
    startedAt: text(stage.startedAt),
    completedAt: text(stage.completedAt),
  };
}


function normalizeRun(value: unknown, stageValues?: unknown): Run {
  const run = record(value);
  const correlationId = text(run.correlationId, "unavailable");
  const stageSource = stageValues ?? run.stages;
  return {
    runId: text(run.runId || run.commandId, "unknown"),
    eventId: text(run.eventId),
    repo: text(run.repository || run.repo || run.system, "Unknown workload"),
    digest: text(run.artifactDigest || run.digest, "Not reported"),
    env: text(run.environment || run.env, "Unknown"),
    system: text(run.system, "Unknown"),
    state: text(run.terminalOutcome || run.status || run.state, "UNKNOWN"),
    correlationId,
    failedStage:
      typeof run.failedStage === "string" ? run.failedStage : null,
    errorCode: typeof run.errorCode === "string" ? run.errorCode : null,
    createdAt: text(run.createdAt),
    updatedAt: text(run.updatedAt),
    stages: records(stageSource).map((stage, index) =>
      normalizeStage(stage, index, correlationId),
    ),
  };
}


function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.trunc(value)
    : 0;
}


function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}


function normalizeCoverage(value: unknown): CollectionCoverage | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const coverage = record(value);
  const counts = record(coverage.counts);
  return {
    manifestId: text(coverage.manifestId) || null,
    state: text(coverage.state) || null,
    counts: {
      expected: count(counts.expected),
      completed: count(counts.completed),
      skipped: count(counts.skipped),
      unsupported: count(counts.unsupported),
      failed: count(counts.failed),
    },
  };
}


function normalizeCollection(value: unknown): RepositoryCollection {
  const collection = record(value);
  const counts = record(collection.counts);
  const commandId = text(collection.commandId || collection.collectionId, "unknown");
  const sourceType = text(collection.sourceType);
  const totals: CollectionCounts = {
    edges: count(counts.edges),
    reads: count(counts.reads),
    writes: count(counts.writes),
    residue: count(counts.residue),
    unresolved: count(counts.unresolved),
  };
  return {
    commandId,
    collectionId: text(collection.collectionId, commandId),
    statusUrl: text(collection.statusUrl, `/api/collections/${commandId}`),
    sourceType:
      sourceType === "LOCAL_CHECKOUT" || sourceType === "GIT" ? sourceType : "UNKNOWN",
    origin: text(collection.origin, "Not reported"),
    repository: text(collection.repository, "Unknown workload"),
    revision: text(collection.revision, "Not reported"),
    environment: text(collection.environment, "Unknown"),
    system: text(collection.system, "Unknown"),
    outcome: text(collection.outcome, "UNKNOWN"),
    reasonCode: text(collection.reasonCode) || null,
    commandStatus: text(collection.commandStatus, "UNKNOWN"),
    terminal: collection.terminal === true,
    runId: text(collection.runId) || null,
    runStatus: text(collection.runStatus) || null,
    proposalId: text(collection.proposalId) || null,
    proposalStatus: text(collection.proposalStatus) || null,
    runtimeStatus: text(collection.runtimeStatus, "NOT_PROVIDED"),
    analysisStatus: text(collection.analysisStatus) || null,
    statusReasons: strings(collection.statusReasons),
    stages: strings(collection.stages),
    counts: totals,
    coverage: normalizeCoverage(collection.coverageManifest ?? collection.coverage),
    correlationId: text(collection.correlationId, "unavailable"),
  };
}


function edgeIds(value: unknown, fallback: LineageEdge[]): string[] {
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return [...value];
  }
  return fallback.map((edge) => edge.edgeKey);
}


function normalizeProposal(value: unknown): Proposal {
  const proposal = record(value);
  const diff = record(proposal.diff);
  const added = records(diff.added) as unknown as LineageEdge[];
  const removed = records(diff.removed) as unknown as LineageEdge[];
  const bandChanged = records(diff.bandChanged) as unknown as LineageEdge[];
  return {
    ...(proposal as unknown as Proposal),
    schemaVersion: text(proposal.schemaVersion, "1.0.0"),
    proposalId: text(proposal.proposalId, "unknown"),
    version: typeof proposal.version === "number" ? proposal.version : 0,
    system: text(proposal.system, "Unknown"),
    state: text(proposal.state, "UNKNOWN"),
    expectedBaseVersion: text(proposal.expectedBaseVersion, "NONE"),
    correlationId: text(proposal.correlationId, "unavailable"),
    createdAt: text(proposal.createdAt),
    updatedAt: text(proposal.updatedAt || proposal.createdAt),
    lockVersion:
      typeof proposal.lockVersion === "number" ? proposal.lockVersion : 0,
    diff: {
      added,
      removed,
      bandChanged,
      addedEdgeIds: edgeIds(diff.addedEdgeIds, added),
      removedEdgeIds: edgeIds(diff.removedEdgeIds, removed),
      bandChangedEdgeIds: edgeIds(diff.bandChangedEdgeIds, bandChanged),
      ...(Object.keys(record(diff.edgeSetRef)).length
        ? { edgeSetRef: record(diff.edgeSetRef) }
        : {}),
    },
  };
}


function normalizeOverview(value: unknown): Overview {
  const overview = record(value);
  const recentRuns = records(overview.recentRuns).map((run) => normalizeRun(run));
  const inReviewSample = records(overview.inReviewSample).map(normalizeProposal);
  const rawCounts = record(overview.counts);
  const embedded = overview.resilience
    ? normalizeResilience(overview.resilience)
    : undefined;
  return {
    environment: text(overview.environment, readRuntimeConfig().environment),
    activeVersion: text(overview.activeVersion) || null,
    fencingToken:
      typeof overview.fencingToken === "number" ? overview.fencingToken : 0,
    counts: {
      runs:
        typeof rawCounts.runs === "number" ? rawCounts.runs : recentRuns.length,
      inReview:
        typeof rawCounts.inReview === "number"
          ? rawCounts.inReview
          : inReviewSample.length,
      quarantined:
        typeof rawCounts.quarantined === "number" ? rawCounts.quarantined : 0,
    },
    countsAreComplete:
      typeof overview.countsAreComplete === "boolean"
        ? overview.countsAreComplete
        : Boolean(overview.counts),
    recentRuns,
    inReviewSample,
    ...(embedded ? { resilience: embedded } : {}),
  };
}


function unavailableResilience(value: Record<string, unknown>): ResilienceSnapshot {
  const pointer = record(value.activeGraph);
  const activeVersion = text(pointer.graphVersion) || null;
  const unavailable = "NOT_AVAILABLE" as const;
  return {
    schemaVersion: "1.0.0",
    capturedAt: null,
    status: unavailable,
    correlation: { status: unavailable, trackedCount: 0, missingCount: 0 },
    queue: {
      status: unavailable,
      depth: 0,
      oldestAgeSeconds: null,
      saturation: { status: unavailable, observedDepth: 0, capacity: null },
      retryCount: 0,
      deadLetterCount: 0,
      leaseStealCount: 0,
    },
    coverage: {
      status: unavailable,
      incompleteCount: 0,
      runtimeJoin: { status: unavailable, joined: 0, eligible: 0, rate: null },
      baseline: { status: unavailable, ageSeconds: null, maxAgeSeconds: 0 },
    },
    review: { status: unavailable, oldestApprovalAgeSeconds: null },
    publication: {
      status: unavailable,
      publishLagSeconds: null,
      pointerPackage: {
        status: unavailable,
        activeVersion,
        packageVersion: null,
      },
      watermark: {
        status: unavailable,
        version: null,
        updatedAt: null,
        ageSeconds: null,
      },
    },
    productionSignals: {
      replication: { status: unavailable, value: null },
      errorBudgetBurn: { status: unavailable, value: null },
      unitCost: { status: unavailable, value: null },
    },
  };
}


function normalizeResilience(value: unknown): ResilienceSnapshot {
  const snapshot = record(value);
  if (!snapshot.queue || !snapshot.coverage || !snapshot.publication) {
    return unavailableResilience(snapshot);
  }
  return snapshot as unknown as ResilienceSnapshot;
}


function normalizeImpact(value: unknown): ImpactResponse {
  const impact = record(value);
  return {
    ...(impact as unknown as ImpactResponse),
    affected: records(impact.affected).map((item) => ({
      urn: text(item.urn),
      system: text(item.system, "Not reported"),
      severity: text(item.severity, "INFO") as ImpactResponse["affected"][number]["severity"],
      band: text(item.band, "LOWEST") as ImpactResponse["affected"][number]["band"],
      corroboration: text(
        item.corroboration,
        "NONE",
      ) as ImpactResponse["affected"][number]["corroboration"],
      pathLength: typeof item.pathLength === "number" ? item.pathLength : 0,
      viaEdges: Array.isArray(item.viaEdges)
        ? item.viaEdges.filter((edge): edge is string => typeof edge === "string")
        : [],
      owner: text(item.owner, "Not reported"),
    })),
  };
}


function pagePath(resource: string, cursor?: string): string {
  const query = new URLSearchParams({ limit: "25" });
  if (cursor) query.set("cursor", cursor);
  return `${resource}?${query.toString()}`;
}


function requireDemoActions(): void {
  if (!readRuntimeConfig().demoActions) {
    throw new Error("Demo actions are available only in local development");
  }
}


export const api = {
  overview: async (signal?: AbortSignal) =>
    normalizeOverview(await request("/overview", {}, signal)),
  resilience: async (signal?: AbortSignal) =>
    normalizeResilience(await request("/operations/resilience", {}, signal)),
  reset: async () => {
    requireDemoActions();
    return (await request("/demo/reset", { method: "POST" })) as DemoReset;
  },
  collect: async (demo: DemoReset["demoDelivery"]) => {
    requireDemoActions();
    const result = (await request("/events/push", {
      method: "POST",
      body: JSON.stringify(demo),
    })) as CollectionResult;
    return {
      ...result,
      run: result.run ? normalizeRun(result.run) : null,
      proposal: result.proposal ? normalizeProposal(result.proposal) : null,
    };
  },
  submitCollection: async (
    body: RepositoryCollectionRequest,
    signal?: AbortSignal,
  ): Promise<RepositoryCollection> =>
    normalizeCollection(
      await request(
        "/collections",
        { method: "POST", body: JSON.stringify(body) },
        signal,
      ),
    ),
  collection: async (
    commandId: string,
    signal?: AbortSignal,
  ): Promise<RepositoryCollection> =>
    normalizeCollection(
      await request(`/collections/${encodeURIComponent(commandId)}`, {}, signal),
    ),
  runs: async (signal?: AbortSignal, cursor?: string): Promise<Page<Run>> => {
    const value = await request(pagePath("/runs", cursor), {}, signal);
    if (Array.isArray(value)) {
      return { items: value.map(normalizeRun), nextCursor: null };
    }
    const page = record(value);
    return {
      items: records(page.items).map((run) => normalizeRun(run)),
      nextCursor: text(page.nextCursor) || null,
    };
  },
  run: async (runId: string, signal?: AbortSignal) => {
    const value = await request(`/runs/${encodeURIComponent(runId)}`, {}, signal);
    const detail = record(value);
    return detail.run
      ? normalizeRun(detail.run, detail.stages)
      : normalizeRun(detail);
  },
  proposals: async (
    signal?: AbortSignal,
    cursor?: string,
  ): Promise<Page<Proposal>> => {
    const value = await request(pagePath("/proposals", cursor), {}, signal);
    if (Array.isArray(value)) {
      return { items: value.map(normalizeProposal), nextCursor: null };
    }
    const page = record(value);
    return {
      items: records(page.items).map(normalizeProposal),
      nextCursor: text(page.nextCursor) || null,
    };
  },
  proposal: async (proposalId: string, signal?: AbortSignal) =>
    normalizeProposal(
      await request(`/proposals/${encodeURIComponent(proposalId)}`, {}, signal),
    ),
  approve: async (
    proposalId: string,
    input: {
      version: number;
      actor: string;
      rationale: string;
      expectedLockVersion: number;
    },
  ): Promise<ReviewResult> => {
    const value = record(
      await request(`/proposals/${encodeURIComponent(proposalId)}/approve`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    );
    const pointer = record(value.pointer);
    return {
      proposal: normalizeProposal(value.proposal),
      ...(typeof value.publication === "string"
        ? { publication: value.publication }
        : {}),
      ...(Object.keys(record(value.approvalRef)).length
        ? { approvalRef: record(value.approvalRef) }
        : {}),
      ...(value.run ? { run: normalizeRun(value.run) } : {}),
      ...(typeof pointer.activeVersion === "string"
        ? { pointer: { activeVersion: pointer.activeVersion } }
        : {}),
    };
  },
  reject: async (
    proposalId: string,
    input: {
      version: number;
      actor: string;
      rationale: string;
      expectedLockVersion: number;
    },
  ): Promise<ReviewResult> => {
    const value = record(
      await request(`/proposals/${encodeURIComponent(proposalId)}/reject`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    );
    return {
      proposal: normalizeProposal(value.proposal),
      ...(Object.keys(record(value.approvalRef)).length
        ? { approvalRef: record(value.approvalRef) }
        : {}),
    };
  },
  lineage: async (
    urn: string,
    direction: "up" | "down",
    depth: number,
    signal?: AbortSignal,
  ) =>
    (await request(
      `/lineage/${encodeURIComponent(urn)}?direction=${direction}&depth=${depth}`,
      {},
      signal,
    )) as LineageResponse,
  edge: async (edgeKey: string, signal?: AbortSignal) =>
    (await request(
      `/edges/${encodeURIComponent(edgeKey)}`,
      {},
      signal,
    )) as LineageEdge,
  impact: async (subject: string, changeType: string, depth: number) =>
    normalizeImpact(
      await request("/impact", {
        method: "POST",
        body: JSON.stringify({ subject, changeType, depth }),
      }),
    ),
};
