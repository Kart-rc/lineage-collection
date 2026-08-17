import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  edge,
  ok,
  overview,
  proposal,
  renderApp,
  run,
  runtimeGlobal,
} from "../test/workspaceFixtures";


afterEach(() => {
  delete runtimeGlobal.__LINEAGE_RUNTIME_CONFIG__;
  vi.unstubAllGlobals();
});


test("run timeline and proposal detail preserve correlation and evidence context", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/runs/run-1") return ok(run);
      if (url === "/api/proposals/proposal-one") return ok(proposal);
      if (url === "/api/edges/edge-one") return ok(edge);
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );

  const timeline = renderApp("/runs/run-1");
  expect(await screen.findByRole("heading", { name: "Run timeline" })).toBeVisible();
  expect(screen.getByText("CLASSIFYING")).toBeVisible();
  expect(screen.getAllByText("corr-1").length).toBeGreaterThan(1);
  timeline.unmount();

  renderApp("/review/proposal-one");
  expect(
    await screen.findByRole("heading", { name: "Awaiting your approval" }),
  ).toBeVisible();
  // The HIGH-band edge lands in the runtime-verified queue with its band projection.
  expect(
    await screen.findByRole("heading", {
      name: "Runtime-verified — corroborated by the execution harness",
    }),
  ).toBeVisible();
  expect((await screen.findAllByText("VERIFIED 92")).length).toBeGreaterThan(0);
  expect(screen.getByText("pipeline.py · 18")).toBeVisible();
  // Provenance evidence keeps the citation, checksum and correlation identity.
  expect(screen.getByText("pipeline.py:18")).toBeVisible();
  expect(screen.getByText(/abc123/)).toBeVisible();
  expect(screen.getAllByText("corr-1").length).toBeGreaterThan(0);
  expect(screen.getAllByText("SUM(amount)").length).toBeGreaterThan(0);
});

test("proposal approval confirms intent and recovers from a concurrent server error", async () => {
  let approvalCalls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/proposals/proposal-one") return ok(proposal);
      if (url === "/api/edges/edge-one") return ok(edge);
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
  await screen.findByRole("heading", { name: "Awaiting your approval" });
  // Wait for the edge fan-out to resolve — approval stays gated until it does.
  await screen.findAllByText("VERIFIED 92");

  await userEvent.type(
    screen.getByLabelText("Review rationale"),
    "Static and runtime evidence agree.",
  );
  expect(screen.getByRole("button", { name: "Reject proposal" })).toBeEnabled();
  const approveButton = screen.getByRole("button", { name: "Approve & publish" });
  expect(approveButton).toBeEnabled();
  await userEvent.click(approveButton);
  expect(await screen.findByRole("alert")).toHaveTextContent("Another reviewer changed");

  await userEvent.click(screen.getByRole("button", { name: "Approve & publish" }));
  const receipt = (
    await screen.findByText("Approval recorded — publication is fenced and durable.")
  ).closest(".decision-receipt") as HTMLElement;
  expect(within(receipt).getByText("v2")).toBeVisible();
  expect(within(receipt).getByRole("link", { name: "Track publication →" })).toHaveAttribute(
    "href",
    "/runs/run-1",
  );
});
