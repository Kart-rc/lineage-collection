import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";

import type { RepositoryCollection } from "../../api/types";
import { MemoryRouter } from "../../routing";
import { CollectionStatus, COLLECTION_POLL_INTERVAL_MS } from "./CollectionStatus";


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
  vi.useRealTimers();
});


function collection(overrides: Partial<RepositoryCollection> = {}): RepositoryCollection {
  return {
    commandId: "cmd-1",
    collectionId: "cmd-1",
    statusUrl: "/api/collections/cmd-1",
    sourceType: "GIT",
    origin: "https://github.com/spring-projects/spring-petclinic",
    repository: "spring-petclinic",
    revision: "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272",
    environment: "staging",
    system: "petclinic",
    outcome: "ACCEPTED",
    reasonCode: null,
    commandStatus: "SUCCEEDED",
    terminal: true,
    runId: "run-1",
    runStatus: "IN_REVIEW",
    proposalId: "prop-1",
    proposalStatus: "IN_REVIEW",
    runtimeStatus: "NOT_PROVIDED",
    analysisStatus: "COMPLETE",
    statusReasons: [],
    stages: ["QUEUED", "ANALYZING", "IN_REVIEW"],
    counts: { edges: 15, reads: 10, writes: 5, residue: 0, unresolved: 0 },
    coverage: {
      manifestId: "manifest-1",
      state: "COMPLETE",
      counts: { expected: 131, completed: 33, skipped: 98, unsupported: 0, failed: 0 },
    },
    correlationId: "corr-1",
    ...overrides,
  };
}


function renderStatus(
  initial: RepositoryCollection,
  onTerminal?: (value: RepositoryCollection) => void,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CollectionStatus
          commandId={initial.commandId}
          initial={initial}
          onTerminal={onTerminal}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}


test("renders stages, counts, coverage and the terminal state", () => {
  renderStatus(collection());

  expect(screen.getByRole("status")).toHaveTextContent("Collection ACCEPTED");
  for (const stage of ["QUEUED", "ANALYZING", "IN_REVIEW"]) {
    expect(screen.getByText(stage)).toBeInTheDocument();
  }
  const counts = screen.getByText("Edges").closest("dl");
  expect(counts).toHaveTextContent("15");
  expect(counts).toHaveTextContent("131");
  expect(counts).toHaveTextContent("98");
  expect(screen.getByText("NOT_PROVIDED")).toBeInTheDocument();
});


test("surfaces the collector-chosen workflow path when reported", () => {
  const withPath = renderStatus(collection({ workflowKind: "INCREMENTAL" }));
  const chip = screen.getByText("INCREMENTAL PATH");
  expect(chip).toBeVisible();
  expect(chip).toHaveAttribute("data-kind", "INCREMENTAL");
  withPath.unmount();

  // Older status documents without workflowKind render no chip at all.
  renderStatus(collection());
  expect(screen.queryByText(/PATH$/)).toBeNull();
});


test("renders per-step wall-clock timings when the document carries them", () => {
  const withTimings = renderStatus(
    collection({
      timings: [
        {
          step: "acquire",
          startedAt: "2026-08-15T13:33:20Z",
          completedAt: "2026-08-15T13:33:33Z",
          durationMs: 13400,
          detail: "shallow fetch spring-petclinic@88e37c15cf6f",
        },
        { step: "B5", startedAt: "2026-08-15T13:33:35Z", completedAt: "2026-08-15T13:33:35Z", durationMs: 840 },
        {
          step: "runtime-harness",
          startedAt: "2026-08-15T13:33:35Z",
          completedAt: "2026-08-15T13:33:36Z",
          durationMs: 1210,
          detail: "javac+java · 8 observations",
        },
      ],
    }),
  );
  expect(screen.getByText(/Pipeline timings/)).toHaveTextContent("15 s wall clock");
  expect(screen.getByText("acquire")).toBeVisible();
  expect(screen.getByText("13 s")).toBeVisible();
  expect(screen.getByText("840 ms")).toBeVisible();
  expect(screen.getByText("javac+java · 8 observations")).toBeVisible();
  withTimings.unmount();

  // Older documents without timings render no timing rail.
  renderStatus(collection());
  expect(screen.queryByText(/Pipeline timings/)).toBeNull();
});


test("links to the generated run and proposal", () => {
  renderStatus(collection());

  expect(screen.getByRole("link", { name: "Open run timeline" })).toHaveAttribute(
    "href",
    "/runs/run-1",
  );
  // The proposal detail route is /review/{id}; /proposals/{id} falls through to
  // the Operations page, which a browser smoke caught and this assertion had missed.
  expect(screen.getByRole("link", { name: "Open proposal" })).toHaveAttribute(
    "href",
    "/review/prop-1",
  );
});


test("reports a terminal collection exactly once", () => {
  const finished: string[] = [];
  const { rerender } = renderStatus(collection(), (value) => finished.push(value.commandId));
  rerender(<div />);

  expect(finished).toEqual(["cmd-1"]);
});


test("does not poll once the durable command is terminal", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  vi.useFakeTimers({ shouldAdvanceTime: true });

  renderStatus(collection({ terminal: true }));
  await vi.advanceTimersByTimeAsync(COLLECTION_POLL_INTERVAL_MS * 4);

  expect(fetcher).not.toHaveBeenCalled();
});


test("polls while the durable command is non-terminal and stops when it settles", async () => {
  const pending = collection({ terminal: false, commandStatus: "QUEUED", outcome: "ACCEPTED" });
  const settled = collection({ terminal: true, commandStatus: "SUCCEEDED" });
  const fetcher = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      ...settled,
      coverageManifest: {
        manifestId: "manifest-1",
        state: "COMPLETE",
        counts: { expected: 131, completed: 33, skipped: 98, unsupported: 0, failed: 0 },
      },
    }),
  });
  vi.stubGlobal("fetch", fetcher);
  vi.useFakeTimers({ shouldAdvanceTime: true });

  renderStatus(pending);
  expect(screen.getByRole("status")).toHaveTextContent("Collection in progress…");

  await vi.advanceTimersByTimeAsync(COLLECTION_POLL_INTERVAL_MS + 50);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  expect(String(fetcher.mock.calls[0][0])).toContain("/api/collections/cmd-1");

  await vi.advanceTimersByTimeAsync(COLLECTION_POLL_INTERVAL_MS * 4);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
