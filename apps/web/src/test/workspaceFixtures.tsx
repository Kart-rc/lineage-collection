import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "../routing";

import { App } from "../App";


export const runtimeGlobal = globalThis as typeof globalThis & {
  __LINEAGE_RUNTIME_CONFIG__?: unknown;
};


export const SOURCE = "urn:ldp:staging:snowflake:payments:raw.transactions#amount";
export const TARGET =
  "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue";

export const overview = {
  activeVersion: "v1",
  fencingToken: 0,
  counts: { runs: 1, inReview: 1, quarantined: 0 },
  recentRuns: [],
  resilience: {
    schemaVersion: "1.0.0",
    capturedAt: "2026-08-06T12:00:00Z",
    status: "OUT_OF_SYNC",
    correlation: { status: "COMPLETE", trackedCount: 8, missingCount: 0 },
    queue: {
      status: "DEGRADED",
      depth: 4,
      oldestAgeSeconds: 95,
      saturation: { status: "NOT_CONFIGURED", observedDepth: 4, capacity: null },
      retryCount: 2,
      deadLetterCount: 1,
      leaseStealCount: 1,
    },
    coverage: {
      status: "INCOMPLETE",
      incompleteCount: 2,
      runtimeJoin: { status: "INCOMPLETE", joined: 3, eligible: 5, rate: 0.6 },
      baseline: { status: "STALE", ageSeconds: 90000, maxAgeSeconds: 86400 },
    },
    review: { status: "DEGRADED", oldestApprovalAgeSeconds: 480 },
    publication: {
      status: "OUT_OF_SYNC",
      publishLagSeconds: 72,
      pointerPackage: { status: "OUT_OF_SYNC", activeVersion: "v1", packageVersion: "v2" },
      watermark: { status: "STALE", version: "v1", updatedAt: "2026-08-05T12:00:00Z", ageSeconds: 86400 },
    },
    productionSignals: {
      replication: { status: "NOT_CONFIGURED", value: null },
      errorBudgetBurn: { status: "NOT_CONFIGURED", value: null },
      unitCost: { status: "NOT_CONFIGURED", value: null },
    },
  },
};

// Live-shaped run ledger entries — one per workflow path.
export const ledgerRuns = [
  {
    runId: "cmd-baseline-1",
    workflowKind: "BASELINE",
    status: "PUBLISHED",
    terminalOutcome: "PUBLISHED",
    currentStageId: "B10",
    system: "payments",
    environment: "staging",
    correlationId: "corr-base",
    createdAt: "2026-08-04T12:00:00Z",
    updatedAt: "2026-08-04T16:00:00Z",
  },
  {
    runId: "cmd-docs-1",
    workflowKind: "INCREMENTAL",
    status: "NO_LINEAGE_IMPACT",
    terminalOutcome: "NO_LINEAGE_IMPACT",
    currentStageId: "I10",
    system: "payments",
    environment: "staging",
    correlationId: "corr-docs",
    createdAt: "2026-08-04T12:00:00Z",
    updatedAt: "2026-08-04T15:00:00Z",
  },
  {
    runId: "cmd-deploy-1",
    workflowKind: "DEPLOYMENT",
    status: "PROMOTED",
    terminalOutcome: "PROMOTED",
    currentStageId: "D6",
    system: "payments",
    environment: "staging",
    correlationId: "corr-deploy",
    createdAt: "2026-08-04T12:00:00Z",
    updatedAt: "2026-08-04T14:00:00Z",
  },
  {
    runId: "cmd-pr-1",
    workflowKind: "PR_GATE",
    status: "BLOCK",
    terminalOutcome: "BLOCK",
    currentStageId: "P8",
    system: "payments",
    environment: "staging",
    correlationId: "corr-pr",
    createdAt: "2026-08-04T12:00:00Z",
    updatedAt: "2026-08-04T13:00:00Z",
  },
];

export const edge = {
  schemaVersion: "1.0.0",
  edgeKey: "edge-one",
  version: 1,
  from: [SOURCE],
  to: TARGET,
  edgeType: "DERIVES",
  band: "HIGH",
  corroboration: "ELEMENT",
  status: "PROPOSED",
  transform: "SUM(amount)",
  provenance: [
    {
      provenanceId: "prov-1",
      from: [SOURCE],
      to: TARGET,
      edgeType: "DERIVES",
      mechanism: "SCA",
      exact: true,
      evidenceRef: { schemaVersion: "1.0.0", kind: "sca", key: "payments/run-1", checksum: "abc123" },
      repo: "payments-pipeline",
      runId: "run-1",
      correlationId: "corr-1",
      transform: "SUM(amount)",
      citation: { file: "pipeline.py", line: 18, astPath: "Module.body[0].body[1]" },
      sessionComplete: true,
    },
  ],
  autoPublishable: true,
  system: "payments",
  updatedAt: "2026-08-04T16:00:00Z",
};

export const proposal = {
  schemaVersion: "1.0.0",
  proposalId: "proposal-one",
  version: 1,
  system: "payments",
  state: "IN_REVIEW",
  expectedBaseVersion: "v1",
  diff: { added: [edge], removed: [], bandChanged: [] },
  correlationId: "corr-1",
  createdAt: "2026-08-04T16:00:00Z",
  updatedAt: "2026-08-04T16:00:00Z",
  lockVersion: 1,
};

export const run = {
  runId: "run-1",
  eventId: "delivery-1",
  repo: "payments-pipeline",
  digest: "demo-digest-v2",
  env: "staging",
  system: "payments",
  state: "IN_REVIEW",
  correlationId: "corr-1",
  failedStage: null,
  errorCode: null,
  createdAt: "2026-08-04T16:00:00Z",
  updatedAt: "2026-08-04T16:00:00Z",
  stages: ["QUEUED", "CLASSIFYING", "ANALYZING", "IN_REVIEW"].map((stage, index) => ({
    sequence: index + 1,
    stage,
    status: "COMPLETED",
    correlationId: "corr-1",
    detail: {},
    startedAt: "2026-08-04T16:00:00Z",
    completedAt: "2026-08-04T16:00:00Z",
  })),
};

export function ok(data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: async () => data });
}

export function renderApp(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
