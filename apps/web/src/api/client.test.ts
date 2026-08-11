import { api } from "./client";


const runtimeGlobal = globalThis as typeof globalThis & {
  __LINEAGE_RUNTIME_CONFIG__?: unknown;
};


beforeEach(() => {
  runtimeGlobal.__LINEAGE_RUNTIME_CONFIG__ = {
    schemaVersion: "1.0.0",
    environment: "production",
    apiBasePath: "/api",
    sourceRevision: "abc123",
    demoActions: false,
  };
});


afterEach(() => {
  delete runtimeGlobal.__LINEAGE_RUNTIME_CONFIG__;
  vi.unstubAllGlobals();
});


test("unwraps bounded production run pages and normalizes run detail", async () => {
  const summary = {
    runId: "cmd-1",
    workflowKind: "INCREMENTAL",
    workflowVersion: "1.0.0",
    currentStageId: "I4",
    currentStageName: "BUILD_CHANGE_COVERAGE_PLAN",
    status: "RUNNING",
    correlationId: "corr-1",
    environment: "staging",
    system: "payments",
    createdAt: "2026-08-08T12:00:00Z",
    updatedAt: "2026-08-08T12:01:00Z",
  };
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [summary], nextCursor: "next-page" }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        run: summary,
        stages: [
          {
            stageId: "I4",
            stageName: "BUILD_CHANGE_COVERAGE_PLAN",
            status: "COMPLETED",
            completedAt: "2026-08-08T12:01:00Z",
            output: { bucket: "evidence", key: "i4.json" },
          },
        ],
      }),
    });
  vi.stubGlobal("fetch", fetcher);

  const page = await api.runs();
  const detail = await api.run("cmd-1");

  expect(page.items[0]).toMatchObject({
    runId: "cmd-1",
    repo: "payments",
    env: "staging",
    state: "RUNNING",
    stages: [],
  });
  expect(page.nextCursor).toBe("next-page");
  expect(detail.stages[0]).toMatchObject({
    sequence: 1,
    stage: "BUILD_CHANGE_COVERAGE_PLAN",
    correlationId: "corr-1",
  });
  expect(fetcher).toHaveBeenNthCalledWith(
    1,
    "/api/runs?limit=25",
    expect.objectContaining({ credentials: "same-origin" }),
  );
});


test("normalizes the production error envelope without leaking response details", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({
        error: { code: "CONCURRENT_DECISION", message: "proposal changed concurrently" },
        correlationId: "corr-2",
      }),
    }),
  );

  await expect(api.proposal("proposal-1")).rejects.toMatchObject({
    code: "CONCURRENT_DECISION",
    correlationId: "corr-2",
    message: "proposal changed concurrently",
  });
});


test("keeps proposal evidence IDs and reports durable publication queuing", async () => {
  const proposal = {
    schemaVersion: "1.0.0",
    proposalId: "proposal-1",
    version: 1,
    system: "payments",
    state: "IN_REVIEW",
    expectedBaseVersion: "graph-v1",
    correlationId: "corr-3",
    createdAt: "2026-08-08T12:00:00Z",
    lockVersion: 1,
    diff: {
      edgeSetRef: { bucket: "evidence", key: "edges.json", versionId: "v1" },
      addedEdgeIds: ["edge-1"],
      removedEdgeIds: [],
      bandChangedEdgeIds: [],
    },
  };
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [proposal], nextCursor: null }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        proposal: { ...proposal, state: "APPROVED", lockVersion: 2 },
        approvalRef: { bucket: "evidence", key: "approval.json" },
        publication: "OUTBOX_PENDING",
      }),
    });
  vi.stubGlobal("fetch", fetcher);

  const page = await api.proposals();
  const result = await api.approve("proposal-1", {
    version: 1,
    expectedLockVersion: 1,
    actor: "reviewer@example.com",
    rationale: "Evidence verified",
  });

  expect(page.items[0].diff.addedEdgeIds).toEqual(["edge-1"]);
  expect(page.items[0].diff.added).toEqual([]);
  expect(result.publication).toBe("OUTBOX_PENDING");
  expect(result.proposal.state).toBe("APPROVED");
  expect(fetcher).toHaveBeenNthCalledWith(
    1,
    "/api/proposals?limit=25",
    expect.any(Object),
  );
});


test("demo endpoints remain unreachable when deployed configuration disables them", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);

  await expect(api.reset()).rejects.toThrow(/local development/);
  expect(fetcher).not.toHaveBeenCalled();
});
