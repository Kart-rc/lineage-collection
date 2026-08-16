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


// A live-shaped run waiting at the human gate, extending the shared ledger.
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

// Detail payload in the live shape: {run, stages} with stageId + output.
const reviewRunDetail = {
  run: reviewRun,
  stages: [
    {
      stageId: "I1",
      status: "COMPLETED",
      correlationId: "corr-review",
      startedAt: "2026-08-04T16:00:00Z",
      completedAt: "2026-08-04T16:00:30Z",
      output: {},
    },
    {
      stageId: "I5",
      status: "COMPLETED",
      correlationId: "corr-review",
      startedAt: "2026-08-04T16:00:30Z",
      completedAt: "2026-08-04T16:02:00Z",
      output: { key: "workflow/cmd-inc-review/i5.json", sha256: "deadbeefcafe1234" },
    },
    {
      stageId: "I9",
      status: "COMPLETED",
      correlationId: "corr-review",
      startedAt: "2026-08-04T16:02:00Z",
      completedAt: "2026-08-04T16:02:41Z",
      output: {},
    },
  ],
};

// Live checkpoint-only shape: no startedAt, lexicographic stage order, an
// UNKNOWN intake stage, second-precision run.createdAt — exactly what the
// floci product API returns.
const baselineRunDetail = {
  run: { ...ledgerRuns[0], createdAt: "2026-08-04T12:00:00Z" },
  stages: [
    {
      stageId: "B1",
      stageName: "DEDUPLICATE_AND_PIN_ACTIVE_BASE",
      status: "COMPLETED",
      completedAt: "2026-08-04T12:00:01.100Z",
      output: {},
    },
    {
      stageId: "B10",
      stageName: "PUBLISH_WITH_FENCED_PROTOCOL",
      status: "COMPLETED",
      completedAt: "2026-08-04T12:00:05.500Z",
      output: {},
    },
    {
      stageId: "B2",
      stageName: "CLASSIFY_CHANGE_SCOPE",
      status: "COMPLETED",
      completedAt: "2026-08-04T12:00:02.200Z",
      output: {},
    },
    {
      stageId: "UNKNOWN",
      status: "COMPLETED",
      completedAt: "2026-08-04T12:00:00.500Z",
      output: {},
    },
  ],
};


function stubLedgerFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/runs?limit=25") return ok({ items: ledger, nextCursor: null });
      if (url === "/api/proposals?limit=25") {
        return ok({ items: [reviewProposal], nextCursor: null });
      }
      if (url === "/api/runs/cmd-inc-review") return ok(reviewRunDetail);
      if (url === "/api/runs/cmd-baseline-1") return ok(baselineRunDetail);
      if (url === "/api/runs/cmd-docs-1") return ok({ run: ledgerRuns[1], stages: [] });
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
}


afterEach(() => {
  delete runtimeGlobal.__LINEAGE_RUNTIME_CONFIG__;
  vi.unstubAllGlobals();
});


test("runs ledger renders stage pipelines with honest per-workflow phases", async () => {
  stubLedgerFetch();
  renderApp("/runs");

  // Status counts derive from the ledger: 5 runs, 1 awaiting, 2 published,
  // 0 running/failed (NO_LINEAGE_IMPACT and BLOCK are settled verdicts).
  expect(await screen.findByRole("button", { name: "All · 5" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Running · 0" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Awaiting approval · 1" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Failed · 0" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Published · 2" })).toBeVisible();

  // Every row wears its six-chip macro-phase pipeline.
  const baselineRow = screen
    .getByLabelText("Stage pipeline for cmd-baseline-1")
    .closest("button") as HTMLElement;
  expect(baselineRow).toHaveAttribute("aria-expanded", "false");
  expect(within(baselineRow).getByText("BASELINE")).toBeVisible();
  expect(within(baselineRow).getByLabelText("PB publish: done")).toBeVisible();
  expect(within(baselineRow).getByLabelText("RV review: done")).toBeVisible();

  // The awaiting run shows the amber human gate on RV, publish still pending.
  const reviewRow = screen.getByLabelText(
    "Stage pipeline for cmd-inc-review",
  ).parentElement as HTMLElement;
  expect(
    within(reviewRow).getByLabelText("RV review: waiting at the human gate"),
  ).toBeVisible();
  expect(within(reviewRow).getByLabelText("PB publish: pending")).toBeVisible();
  expect(within(reviewRow).getByLabelText("PR propose: done")).toBeVisible();

  // Deployment runs render inapplicable phases as honest "—" chips.
  const deployRow = screen.getByLabelText(
    "Stage pipeline for cmd-deploy-1",
  ).parentElement as HTMLElement;
  expect(within(deployRow).getByLabelText("RV review: not applicable")).toBeVisible();
  expect(within(deployRow).getByLabelText("PR propose: not applicable")).toBeVisible();
  expect(within(deployRow).getByLabelText("PB publish: done")).toBeVisible();

  // No edge counts are derivable from the ledger entries, so no numbers appear.
  const rows = screen.getAllByLabelText(/Stage pipeline for/);
  expect(rows).toHaveLength(5);

  // System filter derives from the ledger.
  expect(
    within(screen.getByLabelText("Filter by system")).getByRole("option", {
      name: "payments",
    }),
  ).toBeInTheDocument();
});

test("status chips filter the ledger in place", async () => {
  stubLedgerFetch();
  renderApp("/runs");

  const awaitingChip = await screen.findByRole("button", {
    name: "Awaiting approval · 1",
  });
  expect(awaitingChip).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(awaitingChip);
  expect(awaitingChip).toHaveAttribute("aria-pressed", "true");

  expect(screen.queryByLabelText("Stage pipeline for cmd-baseline-1")).toBeNull();
  expect(screen.getByLabelText("Stage pipeline for cmd-inc-review")).toBeVisible();
  expect(screen.getByText(/1–1 of 5/)).toBeVisible();

  await userEvent.click(screen.getByRole("button", { name: "All · 5" }));
  expect(screen.getByLabelText("Stage pipeline for cmd-baseline-1")).toBeVisible();
});

test("expanding a waiting run offers in-place triage with review and compare", async () => {
  stubLedgerFetch();
  renderApp("/runs");

  const row = (await screen.findByLabelText("Stage pipeline for cmd-inc-review"))
    .closest("button") as HTMLElement;
  await userEvent.click(row);
  expect(row).toHaveAttribute("aria-expanded", "true");

  // Amber gate banner names the proposal and its real diff counts.
  expect(
    await screen.findByText(
      "Waiting at the human gate — proposal-two ready for review",
    ),
  ).toBeVisible();
  expect(screen.getByText(/\+1 −0 ~0 edges · waiting /)).toBeVisible();

  // Triage links: review, compare, and the full detail page.
  expect(screen.getByRole("link", { name: "Review proposal" })).toHaveAttribute(
    "href",
    "/review/proposal-two",
  );
  expect(screen.getByRole("link", { name: "Compare vs baseline" })).toHaveAttribute(
    "href",
    "/runs/compare?candidate=cmd-inc-review",
  );
  expect(screen.getByRole("link", { name: "Full run detail →" })).toHaveAttribute(
    "href",
    "/runs/cmd-inc-review",
  );

  // Stage timeline rows and the checksummed artifact come from the run detail.
  expect(await screen.findByText("I5")).toBeVisible();
  expect(screen.getByText("workflow/cmd-inc-review/i5.json")).toBeVisible();
  expect(screen.getByText(/I5 · deadbeefcafe…/)).toBeVisible();
  expect(screen.getByText(/Stage timeline/i)).toBeVisible();
});

test("expanding a run without artifact refs stays honest", async () => {
  stubLedgerFetch();
  renderApp("/runs");

  const row = (await screen.findByLabelText("Stage pipeline for cmd-docs-1"))
    .closest("button") as HTMLElement;
  await userEvent.click(row);

  expect(
    await screen.findByText("No artifact manifest on this run."),
  ).toBeVisible();
  expect(screen.getByText("No durable stage history on this run.")).toBeVisible();
  // A settled run offers no review action, but compare and detail remain.
  expect(screen.queryByRole("link", { name: "Review proposal" })).toBeNull();
  expect(screen.getByRole("link", { name: "Compare vs baseline" })).toHaveAttribute(
    "href",
    "/runs/compare?candidate=cmd-docs-1",
  );
});

test("checkpoint-only stage histories render the gantt in execution order", async () => {
  stubLedgerFetch();
  renderApp("/runs");

  const row = (await screen.findByLabelText("Stage pipeline for cmd-baseline-1"))
    .closest("button") as HTMLElement;
  await userEvent.click(row);

  // Execution order comes from the completion checkpoints, not the payload's
  // lexicographic stage-id order (B1, B10, B2 …).
  const timeline = await screen.findByRole("list", { name: "Stage timeline" });
  const names = within(timeline)
    .getAllByRole("listitem")
    .map((item) => item.textContent ?? "");
  expect(names[0]).toContain("DEDUPLICATE_AND_PIN_ACTIVE_BASE");
  expect(names[1]).toContain("CLASSIFY_CHANGE_SCOPE");
  expect(names[2]).toContain("PUBLISH_WITH_FENCED_PROTOCOL");

  // Durations derive from the serialized checkpoints: B1 runs from the intake
  // checkpoint (00.5) to 01.1, B2 to 02.2, B10 to 05.5 — never "—".
  expect(names[0]).toContain("600ms");
  expect(names[1]).toContain("1.1s");
  expect(names[2]).toContain("3.3s");
  expect(within(timeline).queryByText("—")).toBeNull();
  // The header totals the serialized wall-clock.
  expect(screen.getByText(/Stage timeline · 5.0s total/)).toBeVisible();
});
