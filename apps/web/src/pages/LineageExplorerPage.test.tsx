import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "../routing";

import { App } from "../App";


const OWNERS = "urn:ldp:staging:mysql:petclinic-rest:owners";
const CONTROLLER =
  "service://spring-petclinic/org.springframework.samples.petclinic.owner.OwnerController#showOwner";

const edge = {
  schemaVersion: "1.0.0",
  edgeKey: "edge-01ab8b527ca6786a9a796f82",
  version: 1,
  from: [OWNERS],
  to: CONTROLLER,
  edgeType: "READS",
  band: "HIGH",
  corroboration: "ELEMENT",
  status: "PROPOSED",
  transform: "OwnerRepository.findById -> owners",
  provenance: [
    {
      provenanceId: "spring-sca-1",
      from: [OWNERS],
      to: CONTROLLER,
      edgeType: "READS",
      mechanism: "SCA",
      exact: true,
      evidenceRef: {
        bucket: "lineage-evidence",
        key: "commands/cmd-seed/sca/evidence.json",
        sha256: "d20bf068aa11",
        sizeBytes: 123599,
      },
      repo: "spring-petclinic",
      runId: "cmd-seed-1",
      correlationId: "corr-seed-1",
      transform: "OwnerRepository.findById -> owners",
      citation: {
        file: "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
        line: 172,
        astPath: "program/class_declaration",
      },
      sessionComplete: true,
    },
  ],
  autoPublishable: true,
  system: "petclinic",
  updatedAt: "2026-08-15T03:41:00Z",
};

function lineageBody(subject: string, overrides: Record<string, unknown> = {}) {
  return {
    subject,
    direction: "both",
    namespaceVersion: "graph-demo",
    depthSearched: 3,
    truncated: false,
    nodes: [
      { urn: OWNERS, system: "petclinic", kind: "DATASET" },
      { urn: CONTROLLER, system: "petclinic", kind: "DATASET" },
    ],
    edges: [edge],
    ...overrides,
  };
}

function ok(data: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: async () => data });
}

function stubFetch(options: { truncated?: boolean } = {}) {
  const lineageUrls: string[] = [];
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/overview")
      return ok({
        environment: "staging",
        activeVersion: "graph-demo",
        fencingToken: 5,
        counts: { runs: 1, inReview: 0, quarantined: 0 },
        recentRuns: [],
      });
    if (url.startsWith("/api/lineage/")) {
      lineageUrls.push(url);
      const subject = decodeURIComponent(url.slice("/api/lineage/".length).split("?")[0]);
      return ok(lineageBody(subject, options.truncated ? { truncated: true } : {}));
    }
    if (url === "/api/impact" && init?.method === "POST")
      return ok({
        subject: OWNERS,
        changeType: "COLUMN_DROP",
        namespaceVersion: "graph-demo",
        depthSearched: 5,
        truncated: false,
        affected: [
          {
            urn: CONTROLLER,
            system: "petclinic",
            severity: "BLOCK",
            band: "HIGH",
            corroboration: "ELEMENT",
            pathLength: 1,
            viaEdges: [edge.edgeKey],
            owner: "team-petclinic",
          },
        ],
        summary: { block: 1, warn: 0, info: 0 },
      });
    throw new Error(`Unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { lineageUrls };
}

function renderExplorer() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/lineage"]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());


test("explorer defaults to the live seed subject walked in both directions", async () => {
  const { lineageUrls } = stubFetch();
  renderExplorer();

  expect(await screen.findByText("Projection graph-demo · depth 3")).toBeVisible();
  expect(screen.getByLabelText("Traversal direction")).toHaveValue("both");
  expect(screen.getByLabelText("Traversal depth")).toHaveValue("3");
  expect(screen.getByLabelText("Subject URN")).toHaveValue(OWNERS);
  expect(lineageUrls[0]).toContain(encodeURIComponent(OWNERS));
  expect(lineageUrls[0]).toContain("direction=both");
  expect(
    screen.getByRole("button", { name: /Select node owners, DATASET/ }),
  ).toBeVisible();
});

test("clicking a node selects it; the walk re-roots via the inspector action", async () => {
  const { lineageUrls } = stubFetch();
  renderExplorer();

  const controllerNode = await screen.findByRole("button", {
    name: /Select node OwnerController#showOwner/,
  });
  await userEvent.click(controllerNode);

  // Selection opens the node summary in the rail — no navigation yet.
  const inspector = screen.getByRole("complementary", { name: "Evidence rail" });
  expect(
    within(inspector).getByRole("heading", { name: "OwnerController#showOwner" }),
  ).toBeVisible();
  expect(within(inspector).getByText(/Incident edges · 1/)).toBeVisible();
  expect(lineageUrls).toHaveLength(1);

  // The explicit action re-roots the walk on the selected node.
  await userEvent.click(
    within(inspector).getByRole("button", { name: /Walk from this node/ }),
  );
  await waitFor(() => expect(lineageUrls.length).toBeGreaterThan(1));
  expect(lineageUrls.at(-1)).toContain(encodeURIComponent(CONTROLLER));
  expect(screen.getByLabelText("Subject URN")).toHaveValue(CONTROLLER);
});

test("Escape clears the selection back to the walk summary", async () => {
  stubFetch();
  renderExplorer();

  const controllerNode = await screen.findByRole("button", {
    name: /Select node OwnerController#showOwner/,
  });
  await userEvent.click(controllerNode);
  const inspector = screen.getByRole("complementary", { name: "Evidence rail" });
  expect(
    within(inspector).getByRole("heading", { name: "OwnerController#showOwner" }),
  ).toBeVisible();

  await userEvent.keyboard("{Escape}");
  expect(
    within(inspector).queryByRole("heading", { name: "OwnerController#showOwner" }),
  ).toBeNull();
  expect(screen.getByRole("heading", { name: "Walk summary" })).toBeVisible();
});

test("a truncated walk is reported to the reviewer", async () => {
  stubFetch({ truncated: true });
  renderExplorer();

  expect(await screen.findByRole("status")).toHaveTextContent(/truncated at the result limit/);
});

test("the rail summarizes the walk before any selection", async () => {
  stubFetch();
  renderExplorer();

  const inspector = await screen.findByRole("complementary", { name: "Evidence rail" });
  expect(within(inspector).getByRole("heading", { name: "Walk summary" })).toBeVisible();
  expect(within(inspector).getByText("1 datasets · 0 elements · 1 services")).toBeVisible();
  expect(within(inspector).getByText(/1 runtime-verified · VERIFIED 92/)).toBeVisible();
  expect(within(inspector).getByText(/0 static-only · PROBABLE 70/)).toBeVisible();
  expect(within(inspector).getByText("Select an edge for its evidence.")).toBeVisible();
});

test("selecting an edge opens the evidence dossier with citation and run link", async () => {
  stubFetch();
  renderExplorer();

  const edgeControl = await screen.findByRole("button", { name: /Inspect edge.*HIGH.*SCA/ });
  edgeControl.focus();
  await userEvent.keyboard("{Enter}");

  expect(
    screen.getByRole("heading", { name: /owners.*reads.*OwnerController#showOwner/ }),
  ).toBeVisible();
  const inspector = screen.getByRole("complementary", { name: "Evidence rail" });
  expect(within(inspector).getByText("VERIFIED 92")).toBeVisible();
  expect(
    within(inspector).getByText("Static analysis and the runtime harness agree at element level."),
  ).toBeVisible();
  expect(within(inspector).getByText(/SCA/)).toBeVisible();
  expect(
    within(inspector).getByText(
      "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java · 172",
    ),
  ).toBeVisible();
  expect(
    within(inspector).getByRole("link", { name: /Run cmd-seed-1/ }),
  ).toHaveAttribute("href", "/runs/cmd-seed-1");
  expect(
    within(inspector).getByRole("link", { name: "Open evidence bundle" }),
  ).toHaveAttribute("href", "/runs/cmd-seed-1");
});

test("the evidence rail collapses to a slim tab and re-expands on edge selection", async () => {
  stubFetch();
  renderExplorer();

  const inspector = await screen.findByRole("complementary", { name: "Evidence rail" });
  await userEvent.click(
    within(inspector).getByRole("button", { name: "Collapse evidence rail" }),
  );
  expect(screen.queryByRole("heading", { name: "Walk summary" })).toBeNull();
  const expand = screen.getByRole("button", { name: "Expand evidence rail" });
  expect(expand).toHaveAttribute("aria-expanded", "false");

  const edgeControl = screen.getByRole("button", { name: /Inspect edge.*HIGH.*SCA/ });
  edgeControl.focus();
  await userEvent.keyboard("{Enter}");
  expect(
    screen.getByRole("heading", { name: /owners.*reads.*OwnerController#showOwner/ }),
  ).toBeVisible();
});

test("re-rooting from a dossier endpoint refetches on that urn", async () => {
  const { lineageUrls } = stubFetch();
  renderExplorer();

  const edgeControl = await screen.findByRole("button", { name: /Inspect edge.*HIGH.*SCA/ });
  edgeControl.focus();
  await userEvent.keyboard("{Enter}");

  const inspector = screen.getByRole("complementary", { name: "Evidence rail" });
  const rootButtons = within(inspector).getAllByRole("button", { name: /Re-root/ });
  await userEvent.click(rootButtons.at(-1)!);

  await waitFor(() => expect(lineageUrls.length).toBeGreaterThan(1));
  expect(lineageUrls.at(-1)).toContain(encodeURIComponent(CONTROLLER));
  expect(screen.getByLabelText("Subject URN")).toHaveValue(CONTROLLER);
});

test("impact analysis runs against the committed subject", async () => {
  stubFetch();
  renderExplorer();

  const changeType = await screen.findByLabelText("Change type");
  expect(changeType.querySelectorAll("option")).toHaveLength(6);
  await userEvent.selectOptions(changeType, "COLUMN_DROP");
  await userEvent.click(screen.getByRole("button", { name: "Run impact analysis" }));

  expect(await screen.findByText("1 block")).toBeVisible();
  expect(screen.getByText("BLOCK")).toBeVisible();
  expect(screen.getByText("Path length 1")).toBeVisible();
  expect(screen.getByText("HIGH band")).toBeVisible();
});
