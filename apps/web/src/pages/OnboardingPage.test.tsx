import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  ok,
  overview,
  proposal,
  renderApp,
  run,
  runtimeGlobal,
} from "../test/workspaceFixtures";


const REVISION = "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272";

afterEach(() => {
  delete runtimeGlobal.__LINEAGE_RUNTIME_CONFIG__;
  vi.unstubAllGlobals();
});


async function fillSourceStep(user: ReturnType<typeof userEvent.setup>) {
  await user.type(
    screen.getByLabelText("Repository origin (HTTPS)"),
    "https://github.com/spring-projects/spring-petclinic",
  );
  await user.type(screen.getByLabelText("Repository name"), "spring-petclinic");
  await user.type(screen.getByLabelText("Exact commit (40 hex)"), REVISION);
  await user.type(screen.getByLabelText("System"), "petclinic");
}


test("deployed onboarding removes local demo controls", async () => {
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

  // Repository collection is the primary workflow in every environment; only the
  // seeded demo and the local-path source mode are development-only.
  renderApp("/onboard");
  expect(await screen.findByText("Collect a repository")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Run seeded collection" })).toBeNull();
  expect(screen.queryByText("Local development only")).toBeNull();
  expect(
    screen.getAllByRole("option").map((option) => (option as HTMLOptionElement).value),
  ).toEqual(["GIT"]);
  expect(screen.queryByLabelText("Development checkout path")).toBeNull();
});


test("the wizard walks source → profile → review while the plan rail builds live", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
  const user = userEvent.setup();
  renderApp("/onboard");

  // Step 1 — source. The rail names the three steps; the plan rail starts empty.
  expect(
    await screen.findByRole("navigation", { name: "Onboarding steps" }),
  ).toBeVisible();
  expect(screen.getByRole("heading", { name: "Source" })).toBeVisible();
  const plan = screen.getByRole("complementary", { name: "Collection plan" });
  expect(within(plan).getByText("Collection plan")).toBeVisible();

  await fillSourceStep(user);
  // The dark plan rail reflects what was typed, live.
  expect(within(plan).getByText("spring-petclinic")).toBeVisible();
  expect(within(plan).getByText(REVISION.slice(0, 12))).toBeVisible();
  expect(within(plan).getByText("petclinic")).toBeVisible();

  // Step 2 — analysis profile echoes the typed repo@revision honestly and
  // presents the default pack as a highlighted decision card.
  await user.click(
    screen.getByRole("button", { name: "Continue to analysis profile" }),
  );
  expect(screen.getByRole("heading", { name: "Analysis profile" })).toBeVisible();
  expect(
    screen.getByText(`spring-petclinic @ ${REVISION.slice(0, 7)}`),
  ).toBeVisible();
  expect(screen.getByText("Default")).toBeVisible();
  expect(screen.getByRole("button", { name: "Change" })).toBeVisible();

  // Step 3 — review shows the full request verbatim, then the submit control.
  await user.click(screen.getByRole("button", { name: "Review & collect" }));
  expect(screen.getByRole("heading", { name: "Review & collect" })).toBeVisible();
  expect(screen.getByText(REVISION)).toBeVisible();
  expect(
    screen.getByText("https://github.com/spring-projects/spring-petclinic"),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Collect repository" })).toBeVisible();
});


test("submitting the wizard posts the collection and tracks the durable command", async () => {
  const posts: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/overview") return ok(overview);
      if (url === "/api/collections" && init?.method === "POST") {
        posts.push(JSON.parse(String(init.body)));
        return ok(
          {
            commandId: "cmd-9",
            collectionId: "cmd-9",
            statusUrl: "/api/collections/cmd-9",
            sourceType: "GIT",
            origin: "https://github.com/spring-projects/spring-petclinic",
            repository: "spring-petclinic",
            revision: REVISION,
            environment: "staging",
            system: "petclinic",
            outcome: "ACCEPTED",
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
            correlationId: "corr-9",
          },
          202,
        );
      }
      throw new Error(`Unexpected fetch ${url}`);
    }),
  );
  const user = userEvent.setup();
  renderApp("/onboard");

  await screen.findByRole("heading", { name: "Source" });
  await fillSourceStep(user);
  await user.click(
    screen.getByRole("button", { name: "Continue to analysis profile" }),
  );
  await user.click(screen.getByRole("button", { name: "Review & collect" }));
  await user.click(screen.getByRole("button", { name: "Collect repository" }));

  // The wizard's tracking state renders the durable collection status.
  expect(
    await screen.findByRole("heading", { name: "Tracking the collection" }),
  ).toBeVisible();
  expect(screen.getByText("Collection ACCEPTED · run IN_REVIEW")).toBeVisible();
  expect(screen.getByRole("link", { name: "Open run timeline" })).toHaveAttribute(
    "href",
    "/runs/run-1",
  );
  expect(screen.getByRole("link", { name: "Open proposal" })).toHaveAttribute(
    "href",
    "/review/prop-1",
  );

  // The exact request the form serialized reached the wire unchanged.
  expect(posts).toEqual([
    {
      sourceType: "GIT",
      origin: "https://github.com/spring-projects/spring-petclinic",
      repository: "spring-petclinic",
      revision: REVISION,
      environment: "staging",
      platform: "postgres",
      system: "petclinic",
      analyzerPack: "java-spring-data-jpa-v1",
      ruleset: "spring-data-rules-v1",
      schemaProfile: "postgres",
    },
  ]);
});


test("onboarding runs the signed seeded collection", async () => {
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
  renderApp("/onboard");

  expect(
    await screen.findByRole("button", { name: "Run seeded collection" }),
  ).toBeVisible();
  expect(screen.getByText("Local development only")).toBeVisible();

  await userEvent.click(screen.getByRole("button", { name: "Run seeded collection" }));
  expect(await screen.findByText("Delivery accepted into review")).toBeVisible();
  expect(screen.getByRole("link", { name: "Open run timeline" })).toHaveAttribute(
    "href",
    "/runs/run-1",
  );
});
