import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";


const SOURCE = "urn:ldp:staging:snowflake:payments:raw.transactions#amount";
const TARGET = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue";
const edge = {
  schemaVersion: "1.0.0",
  edgeKey: "edge-one",
  version: 2,
  from: [SOURCE],
  to: TARGET,
  edgeType: "DERIVES",
  band: "HIGH",
  corroboration: "ELEMENT",
  status: "PUBLISHED",
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
  updatedAt: "2026-08-04T18:00:00Z",
};

function ok(data: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: async () => data });
}

afterEach(() => vi.unstubAllGlobals());

test("explorer exposes versioned directional graph, evidence inspector, and impact controls", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview")
        return ok({ activeVersion: "v2", fencingToken: 1, counts: { runs: 1, inReview: 0, quarantined: 0 }, recentRuns: [] });
      if (url.startsWith("/api/lineage/"))
        return ok({
          subject: SOURCE,
          direction: "down",
          namespaceVersion: "v2",
          depthSearched: 3,
          truncated: false,
          nodes: [
            { urn: SOURCE, system: "payments", kind: "ELEMENT" },
            { urn: TARGET, system: "payments", kind: "ELEMENT" },
          ],
          edges: [edge],
        });
      if (url === "/api/impact")
        return ok({
          subject: SOURCE,
          changeType: "COLUMN_DROP",
          namespaceVersion: "v2",
          depthSearched: 5,
          truncated: false,
          affected: [
            { urn: TARGET, system: "payments", severity: "BLOCK", band: "HIGH", corroboration: "ELEMENT", pathLength: 1, viaEdges: ["edge-one"], owner: "team-payments" },
          ],
          summary: { block: 1, warn: 0, info: 0 },
        });
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={["/lineage"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("Projection v2")).toBeVisible();
  expect(screen.getByLabelText("Traversal direction")).toHaveValue("down");
  expect(screen.getByLabelText("Traversal depth")).toHaveValue("3");
  expect(screen.getByRole("button", { name: /Select node.*raw.transactions.*amount/ })).toBeVisible();

  const edgeControl = screen.getByRole("button", { name: /Inspect edge.*HIGH.*SCA/ });
  edgeControl.focus();
  await userEvent.keyboard("{Enter}");
  expect(screen.getByRole("heading", { name: "Edge evidence" })).toBeVisible();
  expect(screen.getByText("HIGH confidence")).toBeVisible();
  expect(screen.getByText("SCA")).toBeVisible();
  expect(screen.getByText("pipeline.py:18")).toBeVisible();

  const changeType = screen.getByLabelText("Change type");
  expect(changeType.querySelectorAll("option")).toHaveLength(6);
  await userEvent.selectOptions(changeType, "COLUMN_DROP");
  await userEvent.click(screen.getByRole("button", { name: "Run impact analysis" }));
  expect(await screen.findByText("1 block")).toBeVisible();
  expect(screen.getByText("BLOCK")).toBeVisible();
  expect(screen.getByText("Path length 1")).toBeVisible();
  expect(screen.getByText("HIGH band")).toBeVisible();
});
