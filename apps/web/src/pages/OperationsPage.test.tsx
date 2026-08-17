import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  ledgerRuns,
  ok,
  overview,
  renderApp,
  runtimeGlobal,
} from "../test/workspaceFixtures";


afterEach(() => {
  delete runtimeGlobal.__LINEAGE_RUNTIME_CONFIG__;
  vi.unstubAllGlobals();
});


test("operations stays read-only and routes collection to onboarding", async () => {
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
      if (url === "/api/runs?limit=25") return ok({ items: [], nextCursor: null });
      if (url === "/api/operations/resilience") {
        // A legacy backend that never learned the snapshot shape — the client
        // must fall back to the honest all-NOT_AVAILABLE snapshot.
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

  // Operations is a pure metrics surface — no collection form in any environment.
  renderApp("/");
  // The control plane reports nothing here, so the verdict must not claim health.
  expect(
    await screen.findByRole("heading", { name: "Plane health is not reported" }),
  ).toBeVisible();
  expect(screen.queryByText("The plane is healthy")).toBeNull();
  // Every signal card carries the honest NOT_AVAILABLE status from the snapshot.
  expect(screen.getAllByText("Not available").length).toBeGreaterThanOrEqual(6);
  // The empty ledger yields honest empty states, not fabricated coverage.
  expect(screen.getByText("No runs started in the last 24 h.")).toBeVisible();
  expect(
    screen.getByText(
      "No system has been collected yet — onboarding a repository starts the first baseline.",
    ),
  ).toBeVisible();
  expect(screen.queryByText("Collect a repository")).toBeNull();
  expect(screen.getByRole("link", { name: /Onboard a repository/ })).toHaveAttribute(
    "href",
    "/onboard",
  );
});

test("operations renders live signals from the standalone resilience route", async () => {
  // The shape the AWS projection emits when overview does not embed a snapshot.
  const snapshot = {
    schemaVersion: "1.0.0",
    capturedAt: "2026-08-06T12:00:00Z",
    status: "HEALTHY",
    correlation: { status: "COMPLETE", trackedCount: 3, missingCount: 0 },
    queue: {
      status: "HEALTHY",
      depth: 2,
      oldestAgeSeconds: 12,
      saturation: { status: "NOT_CONFIGURED", observedDepth: 2, capacity: null },
      retryCount: 0,
      deadLetterCount: 0,
      leaseStealCount: 0,
    },
    coverage: {
      status: "COMPLETE",
      incompleteCount: 0,
      runtimeJoin: { status: "COMPLETE", joined: 2, eligible: 2, rate: 1 },
      baseline: { status: "CURRENT", ageSeconds: 1800, maxAgeSeconds: 86400 },
    },
    review: { status: "HEALTHY", oldestApprovalAgeSeconds: null },
    publication: {
      status: "HEALTHY",
      publishLagSeconds: null,
      pointerPackage: { status: "IN_SYNC", activeVersion: "graph-v7", packageVersion: "graph-v7" },
      watermark: { status: "CURRENT", version: "graph-v7", updatedAt: "2026-08-06T11:58:00Z", ageSeconds: 120 },
    },
    productionSignals: {
      replication: { status: "NOT_CONFIGURED", value: null },
      errorBudgetBurn: { status: "NOT_CONFIGURED", value: null },
      unitCost: { status: "NOT_CONFIGURED", value: null },
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") {
        return ok({
          environment: "staging",
          activeVersion: "graph-v7",
          fencingToken: 42,
          recentRuns: [],
          inReviewSample: [],
          countsAreComplete: false,
        });
      }
      if (url === "/api/runs?limit=25") return ok({ items: [], nextCursor: null });
      if (url === "/api/operations/resilience") return ok(snapshot);
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
  renderApp("/");

  expect(
    await screen.findByRole("heading", { name: "The plane is healthy" }),
  ).toBeVisible();
  expect(screen.getByText(/All six signals nominal/)).toBeVisible();
  expect(screen.queryByText("Not available")).toBeNull();
  expect(screen.getByText(/3 tracked · 0 missing/)).toBeVisible();
});

test("operations leads with a verdict, honest signals and a derived triage queue", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/runs?limit=25") return ok({ items: ledgerRuns, nextCursor: null });
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
  renderApp("/");

  // The verdict banner derives from the OUT_OF_SYNC snapshot — never "healthy".
  expect(
    await screen.findByRole("heading", { name: "The plane is out of sync" }),
  ).toBeVisible();
  expect(screen.queryByText("The plane is healthy")).toBeNull();
  expect(
    screen.getByText(/1 of 6 signals nominal · 1 awaiting review — oldest has waited 8m 0s/),
  ).toBeVisible();

  // Six live signal cards with statuses straight from the snapshot.
  for (const label of [
    "Queue",
    "Correlation",
    "Coverage",
    "Review latency",
    "Publication",
    "Control plane",
  ]) {
    expect(screen.getByText(label)).toBeVisible();
  }
  expect(screen.getAllByText("degraded").length).toBeGreaterThan(0);
  expect(screen.getAllByText("incomplete").length).toBeGreaterThan(0);
  expect(screen.getAllByText("out of sync").length).toBeGreaterThan(0);
  expect(screen.getByText("60%")).toBeVisible();
  expect(screen.getByText("runtime join")).toBeVisible();

  // The triage queue is derived from real durable state.
  expect(screen.getByText("1 proposal awaiting review")).toBeVisible();
  expect(screen.getByRole("link", { name: "Open queue" })).toHaveAttribute(
    "href",
    "/review",
  );
  expect(screen.getByText("1 message in the dead-letter queue")).toBeVisible();
  expect(screen.getByText("Baseline is stale")).toBeVisible();
  expect(screen.getByText("PR gate blocked a merge")).toBeVisible();
  expect(screen.getByRole("link", { name: "Open run" })).toHaveAttribute(
    "href",
    "/runs/cmd-pr-1",
  );

  // The recent-runs ledger links every run to its durable timeline.
  expect(screen.getByRole("link", { name: /cmd-baseline-1/ })).toHaveAttribute(
    "href",
    "/runs/cmd-baseline-1",
  );
  expect(screen.getByRole("link", { name: "All runs →" })).toHaveAttribute(
    "href",
    "/runs",
  );
  // The correlation invariant cites the real tracked/missing counts.
  expect(screen.getByText(/8 tracked · 0 missing/)).toBeVisible();

  // Coverage by system groups the ledger — real env, real published baseline.
  const coverageTable = screen.getByRole("table");
  expect(within(coverageTable).getByText("payments")).toBeVisible();
  expect(within(coverageTable).getByText("staging")).toBeVisible();
  expect(within(coverageTable).getByText("PUBLISHED")).toBeVisible();

  // Collection itself lives on the onboarding surface.
  expect(screen.queryByText("Collect a repository")).toBeNull();
  expect(screen.getByRole("link", { name: /Onboard a repository/ })).toHaveAttribute(
    "href",
    "/onboard",
  );
});

test("operations deployment control starts the pipeline and reads back a 409 honestly", async () => {
  let deploymentCalls = 0;
  const fetcher = vi.fn((input: string | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview") return ok(overview);
    if (url === "/api/runs?limit=25") return ok({ items: ledgerRuns, nextCursor: null });
    if (url === "/api/deployments" && init?.method === "POST") {
      deploymentCalls += 1;
      if (deploymentCalls === 1) {
        return ok(
          {
            error: {
              code: "NO_PACKAGE_FOR_SYSTEM",
              message: "no published package",
            },
            correlationId: "corr-deploy-ui",
          },
          409,
        );
      }
      return ok({ commandId: "cmd-deploy-ui-1" }, 202);
    }
    throw new Error(`Unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  renderApp("/");

  const control = (await screen.findByRole("form", {
    name: "Deployment pipeline control",
  })) as HTMLFormElement;
  // The system options stream in from the run ledger.
  await within(control).findByRole("option", { name: "payments" });
  expect(within(control).getByLabelText("System")).toHaveValue("payments");

  // First attempt: the system has no approved package yet.
  await userEvent.click(
    within(control).getByRole("button", { name: "Run deployment pipeline" }),
  );
  expect(await within(control).findByRole("alert")).toHaveTextContent(
    "No published package for this system yet — approve a baseline first.",
  );

  // Second attempt succeeds and links to the durable deployment run.
  await userEvent.click(
    within(control).getByRole("button", { name: "Run deployment pipeline" }),
  );
  expect(await within(control).findByText("Deployment pipeline started")).toBeVisible();
  expect(within(control).getByRole("link", { name: "cmd-deploy-ui-1" })).toHaveAttribute(
    "href",
    "/runs/cmd-deploy-ui-1",
  );

  // The trigger posts the system + the overview environment, nothing else.
  const deployCall = fetcher.mock.calls.find(
    ([input]) => String(input) === "/api/deployments",
  );
  expect(JSON.parse(String((deployCall?.[1] as RequestInit).body))).toEqual({
    system: "payments",
    environment: "local",
  });
});
