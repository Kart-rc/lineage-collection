import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "../routing";

import { App } from "../App";


const runtimeGlobal = globalThis as typeof globalThis & {
  __LINEAGE_RUNTIME_CONFIG__?: unknown;
};


const SOURCE = "urn:ldp:staging:snowflake:payments:raw.transactions#amount";
const TARGET = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue";

const overview = {
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

const edge = {
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

const proposal = {
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

const run = {
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

function ok(data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: async () => data });
}

function renderApp(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  delete runtimeGlobal.__LINEAGE_RUNTIME_CONFIG__;
  vi.unstubAllGlobals();
});


test("deployed operations removes local demo controls", async () => {
  runtimeGlobal.__LINEAGE_RUNTIME_CONFIG__ = {
    schemaVersion: "1.0.0",
    environment: "production",
    apiBasePath: "/api",
    sourceRevision: "abc123",
    demoActions: false,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") {
        return ok({
          environment: "production",
          activeVersion: "graph-v7",
          fencingToken: 42,
          recentRuns: [],
          inReviewSample: [],
          countsAreComplete: false,
        });
      }
      if (url === "/api/operations/resilience") {
        return ok({
          environment: "production",
          activeGraph: { graphVersion: "graph-v7", fence: 42 },
          runtimeControls: [],
          controlPlaneStatus: "AVAILABLE",
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );

  renderApp("/");

  // Repository collection is the primary workflow in every environment; only the
  // seeded demo and the local-path source mode are development-only.
  expect(await screen.findByText("Collect a repository")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Run seeded collection" })).toBeNull();
  expect(screen.queryByText("Seeded demonstration")).toBeNull();
  expect(
    screen.getAllByRole("option").map((option) => (option as HTMLOptionElement).value),
  ).toEqual(["GIT"]);
  expect(screen.queryByLabelText("Development checkout path")).toBeNull();
});

test("operations exposes gate values and runs the signed seeded collection", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/demo/reset")
        return ok({
          activeVersion: "v1",
          catalogDigest: "digest",
          catalogDatasetCount: 3,
          demoDelivery: { payload: { eventId: "delivery-1" }, signature: "sha256=signed" },
        });
      if (url === "/api/events/push")
        return ok({ outcome: "ACCEPTED", reason: null, eventId: "delivery-1", run, proposal }, 202);
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
  renderApp("/");

  expect(await screen.findByText("Active projection")).toBeVisible();
  expect(await screen.findByText("v1")).toBeVisible();
  expect(screen.getByText("Human review")).toBeVisible();
  expect(screen.getByText("1 waiting")).toBeVisible();
  expect(screen.getByText("Operational snapshot")).toBeVisible();
  expect(screen.getAllByText("DEGRADED").length).toBeGreaterThan(0);
  expect(screen.getAllByText("INCOMPLETE").length).toBeGreaterThan(0);
  expect(screen.getAllByText("STALE").length).toBeGreaterThan(0);
  expect(screen.getAllByText("OUT OF SYNC").length).toBeGreaterThan(0);
  expect(screen.getAllByText("NOT CONFIGURED").length).toBeGreaterThan(0);
  for (const label of [
    "Oldest queue age",
    "Queue saturation",
    "Retries / DLQ",
    "Lease steals",
    "Incomplete coverage",
    "Runtime join rate",
    "Baseline freshness",
    "Approval age",
    "Publish lag",
    "Pointer / package",
    "Projection watermark",
    "Replication",
    "Error-budget burn",
    "Unit cost",
  ]) {
    expect(screen.getByText(label)).toBeVisible();
  }

  await userEvent.click(screen.getByRole("button", { name: "Run seeded collection" }));
  expect(await screen.findByText("Delivery accepted into review")).toBeVisible();
  expect(screen.getByRole("link", { name: "Open run timeline" })).toHaveAttribute(
    "href",
    "/runs/run-1",
  );
});

test("run timeline and proposal detail preserve correlation and evidence context", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/runs/run-1") return ok(run);
      if (url === "/api/proposals/proposal-one") return ok(proposal);
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );

  const timeline = renderApp("/runs/run-1");
  expect(await screen.findByRole("heading", { name: "Run timeline" })).toBeVisible();
  expect(screen.getByText("CLASSIFYING")).toBeVisible();
  expect(screen.getAllByText("corr-1").length).toBeGreaterThan(1);
  timeline.unmount();

  renderApp("/review/proposal-one");
  expect(await screen.findByRole("heading", { name: "Proposal review" })).toBeVisible();
  expect(screen.getByText("HIGH confidence")).toBeVisible();
  expect(screen.getByText("ELEMENT corroboration")).toBeVisible();
  expect(screen.getByText("SCA")).toBeVisible();
  expect(screen.getByText("pipeline.py:18")).toBeVisible();
  expect(screen.getByText(/abc123/)).toBeVisible();
});

test("proposal approval confirms intent and recovers from a concurrent server error", async () => {
  let approvalCalls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/proposals/proposal-one") return ok(proposal);
      if (url.endsWith("/approve")) {
        approvalCalls += 1;
        if (approvalCalls === 1) {
          return ok(
            { code: "CONCURRENT_DECISION", message: "Another reviewer changed the proposal", correlationId: "corr-1" },
            409,
          );
        }
        return ok({ proposal: { ...proposal, state: "FINALIZED" }, run, pointer: { activeVersion: "v2" } });
      }
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
  renderApp("/review/proposal-one");
  await screen.findByRole("heading", { name: "Proposal review" });

  await userEvent.type(
    screen.getByLabelText("Review rationale"),
    "Static and runtime evidence agree.",
  );
  expect(screen.getByRole("button", { name: "Reject proposal" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "Approve for publication" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Another reviewer changed");

  await userEvent.click(screen.getByRole("button", { name: "Approve for publication" }));
  expect(await screen.findByText("Published into active graph v2")).toBeVisible();
});
