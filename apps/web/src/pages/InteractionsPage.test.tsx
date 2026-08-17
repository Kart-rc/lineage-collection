import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ok, overview, renderApp, runtimeGlobal } from "../test/workspaceFixtures";


const petclinic = {
  schemaVersion: "1.0.0",
  system: "petclinic",
  service: "spring-petclinic",
  revision: "e9b54f91836d6716650db48fb55f17aec00af86f",
  commandId: "cmd-ui-832750a0a7e4",
  correlationId: "corr-ix-1",
  mechanism: "SCA",
  inbound: [
    {
      channel: "REST",
      operation: "GET /owners/{ownerId}",
      handler: "OwnerController#showOwner",
      requestFields: [{ name: "ownerId", type: "int", classification: "NONE" }],
      responseFields: [{ name: "body", type: "ModelAndView", classification: "NONE" }],
      citation: { file: "owner/OwnerController.java", line: 170 },
    },
    {
      channel: "REST",
      operation: "POST /owners/new",
      handler: "OwnerController#processCreationForm",
      requestFields: [{ name: "owner", type: "Owner", classification: "PII" }],
      responseFields: [],
      citation: { file: "owner/OwnerController.java", line: 78 },
    },
  ],
  outbound: [
    {
      fromService: "spring-petclinic",
      toService: "vet-service",
      channel: "GRPC",
      operation: "vets.List()",
      handler: "VetsClient#list",
      citation: { file: "vet/VetsClient.java", line: 31 },
    },
  ],
  residue: [
    { code: "ambiguous-http-method", path: "system/CrashController.java", line: 12, symbol: "CrashController" },
  ],
};

function stubFetch(items: unknown[] = [petclinic]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/interactions")
        return ok({ schemaVersion: "1.0.0", items });
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
}

afterEach(() => {
  delete runtimeGlobal.__LINEAGE_RUNTIME_CONFIG__;
  vi.unstubAllGlobals();
});


test("the matrix maps callers to callees with honest declared-surface rows", async () => {
  stubFetch();
  renderApp("/interactions");

  expect(
    await screen.findByRole("heading", {
      name: "Service-to-service traffic — the second lineage plane",
    }),
  ).toBeVisible();

  const matrix = await screen.findByRole("table");
  // Inbound endpoints sit on the declared-surface row — callers are unknown.
  expect(within(matrix).getByText("declared surface")).toBeVisible();
  expect(
    within(matrix).getByText("inbound endpoints — callers unknown"),
  ).toBeVisible();
  // Outbound calls attribute real callers to real callees: spring-petclinic is
  // both a callee column (its declared surface) and a caller row (its gRPC call).
  expect(
    within(matrix).getByRole("columnheader", { name: "spring-petclinic" }),
  ).toBeVisible();
  expect(
    within(matrix).getByRole("rowheader", { name: "spring-petclinic" }),
  ).toBeVisible();
  expect(
    within(matrix).getByRole("columnheader", { name: "vet-service" }),
  ).toBeVisible();

  // Residue is surfaced, never silently dropped.
  expect(screen.getByText("1 residue")).toBeVisible();
  // The collected-systems strip links the plane back to its collection run.
  expect(
    screen.getByRole("link", { name: /Collected by cmd-ui-832750a0a7e4/ }),
  ).toHaveAttribute("href", "/runs/cmd-ui-832750a0a7e4");
});

test("selecting a cell opens the operation detail with schemas and PII flags", async () => {
  stubFetch();
  renderApp("/interactions");

  await screen.findByRole("table");
  await userEvent.click(
    screen.getByRole("button", {
      name: "2 operations from declared surface to spring-petclinic",
    }),
  );

  const rail = screen.getByRole("complementary", { name: "Interaction detail" });
  expect(within(rail).getByText("GET /owners/{ownerId}")).toBeVisible();
  expect(within(rail).getByText("POST /owners/new")).toBeVisible();
  expect(
    within(rail).getByText(/OwnerController#showOwner · OwnerController.java · 170/),
  ).toBeVisible();
  // PII classification travels with the field metadata.
  expect(within(rail).getByText("PII")).toBeVisible();
  // Every operation cites its collection run.
  expect(
    within(rail).getAllByRole("link", { name: /Run cmd-ui-832750a0a7e4/ }).length,
  ).toBeGreaterThan(0);
});

test("an empty projection yields an honest empty state", async () => {
  stubFetch([]);
  renderApp("/interactions");

  expect(
    await screen.findByText(/No interactions collected yet/),
  ).toBeVisible();
  expect(screen.queryByRole("table")).toBeNull();
});
