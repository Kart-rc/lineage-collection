import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  ledgerRuns,
  ok,
  overview,
  proposal,
  renderApp,
  runtimeGlobal,
} from "../test/workspaceFixtures";


const reviewRun = {
  runId: "cmd-inc-review",
  workflowKind: "INCREMENTAL",
  status: "AWAITING_APPROVAL",
  currentStageId: "I9",
  system: "payments",
  environment: "staging",
  correlationId: "corr-review",
  createdAt: "2026-08-04T12:00:00Z",
  updatedAt: "2026-08-04T17:00:00Z",
};

const ledger = [...ledgerRuns, reviewRun];

const reviewProposal = {
  ...proposal,
  proposalId: "proposal-two",
  commandId: "cmd-inc-review",
  correlationId: "corr-review",
  state: "IN_REVIEW",
  createdAt: "2026-08-04T17:00:00Z",
};

const reviewRunDetail = { run: reviewRun, stages: [] };

const baselineRunDetail = {
  run: { ...ledgerRuns[0], artifactDigest: "sha256:88e37c1ab2cd4ef01234" },
  stages: [],
};


function stubCompareFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/runs?limit=25") return ok({ items: ledger, nextCursor: null });
      if (url === "/api/runs/cmd-inc-review") return ok(reviewRunDetail);
      if (url === "/api/runs/cmd-baseline-1") return ok(baselineRunDetail);
      if (url === "/api/proposals?limit=25") {
        return ok({ items: [reviewProposal], nextCursor: null });
      }
      if (url === "/api/proposals/proposal-two") return ok(reviewProposal);
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
}

function setLocation(url: string) {
  window.history.replaceState({}, "", url);
}


afterEach(() => {
  delete runtimeGlobal.__LINEAGE_RUNTIME_CONFIG__;
  vi.unstubAllGlobals();
  setLocation("/");
});


test("compare derives header, delta chips and diff rows from the proposal", async () => {
  stubCompareFetch();
  setLocation("/runs/compare?candidate=cmd-inc-review");
  renderApp("/runs/compare");

  // Candidate tile: run id, kind chip and honest state chip.
  expect(await screen.findByText("cmd-inc-review")).toBeVisible();
  expect(await screen.findByText("AWAITING APPROVAL")).toBeVisible();
  expect(screen.getByText("INCREMENTAL")).toBeVisible();

  // Base defaults to the latest published baseline run of the same system.
  expect(await screen.findByText("cmd-baseline-1")).toBeVisible();
  expect(screen.getByText("BASELINE")).toBeVisible();
  expect(await screen.findByText(/rev 88e37c1ab2cd/)).toBeVisible();

  // Delta chips derive from the proposal diff; "unchanged" is not computable
  // from the run records, so no unchanged chip is invented.
  expect(await screen.findByRole("button", { name: "+1 added" })).toBeVisible();
  expect(screen.getByRole("button", { name: "0 removed" })).toBeVisible();
  expect(screen.getByRole("button", { name: "0 confidence shifts" })).toBeVisible();
  expect(screen.queryByText(/unchanged/)).toBeNull();

  // The diff row: ADDED tag, mono edge, confidence projection, evidence class.
  expect(await screen.findByText("ADDED")).toBeVisible();
  expect(
    screen.getByText("raw.transactions#amount → analytics.daily_revenue#gross_revenue"),
  ).toBeVisible();
  expect(screen.getByText("— → VERIFIED 92")).toBeVisible();
  expect(screen.getByText("RUNTIME-VERIFIED")).toBeVisible();
  expect(screen.getByRole("link", { name: "Evidence →" })).toHaveAttribute(
    "href",
    "/review/proposal-two",
  );

  // Approval lives on the review surface — the footer links there honestly.
  const footer = screen.getByText(/fenced pointer swap/).closest(
    ".compare-footer",
  ) as HTMLElement;
  expect(within(footer).getByRole("link", { name: "Review & approve →" })).toHaveAttribute(
    "href",
    "/review/proposal-two",
  );
  expect(within(footer).getByRole("link", { name: "Review & reject" })).toHaveAttribute(
    "href",
    "/review/proposal-two",
  );
});

test("delta chips act as filters on the diff table", async () => {
  stubCompareFetch();
  setLocation("/runs/compare?candidate=cmd-inc-review");
  renderApp("/runs/compare");

  expect(await screen.findByText("ADDED")).toBeVisible();
  const removedChip = screen.getByRole("button", { name: "0 removed" });
  expect(removedChip).toHaveAttribute("aria-pressed", "false");

  await userEvent.click(removedChip);
  expect(removedChip).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByText("ADDED")).toBeNull();
  expect(screen.getByText("No diff rows match the current filters.")).toBeVisible();

  // Clicking again clears the filter.
  await userEvent.click(removedChip);
  expect(await screen.findByText("ADDED")).toBeVisible();
});

test("compare is honest when no candidate is selected", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/runs?limit=25") return ok({ items: ledger, nextCursor: null });
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
  setLocation("/runs/compare");
  renderApp("/runs/compare");

  expect(await screen.findByText(/No candidate run selected/)).toBeVisible();
  expect(screen.getByRole("link", { name: "execution ledger" })).toHaveAttribute(
    "href",
    "/runs",
  );
});

test("compare reports a candidate the ledger does not know", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/runs?limit=25") return ok({ items: ledger, nextCursor: null });
      if (url === "/api/runs/cmd-ghost") {
        return ok(
          { error: { code: "NOT_FOUND", message: "no such run" }, correlationId: "corr-x" },
          404,
        );
      }
      if (url === "/api/proposals?limit=25") return ok({ items: [], nextCursor: null });
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
  setLocation("/runs/compare?candidate=cmd-ghost");
  renderApp("/runs/compare");

  expect(
    await screen.findByText("Run cmd-ghost could not be found in the ledger."),
  ).toBeVisible();
});
