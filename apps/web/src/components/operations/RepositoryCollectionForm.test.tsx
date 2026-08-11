import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "../../api/client";
import type { RepositoryCollectionRequest } from "../../api/types";
import { RepositoryCollectionForm } from "./RepositoryCollectionForm";


const REVISION = "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272";

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
});


async function fillGitForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(
    screen.getByLabelText("Repository origin (HTTPS)"),
    "https://github.com/spring-projects/spring-petclinic",
  );
  await user.type(screen.getByLabelText("Repository name"), "spring-petclinic");
  await user.type(screen.getByLabelText("Exact commit (40 hex)"), REVISION);
  await user.type(screen.getByLabelText("System"), "petclinic");
}


test("serializes a GIT submission without a checkout path", async () => {
  const user = userEvent.setup();
  const submissions: RepositoryCollectionRequest[] = [];
  render(
    <RepositoryCollectionForm
      onSubmit={(body) => submissions.push(body)}
      pending={false}
      allowLocalCheckout={false}
    />,
  );

  await fillGitForm(user);
  await user.click(screen.getByRole("button", { name: "Collect repository" }));

  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toEqual({
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
  });
  expect(submissions[0]).not.toHaveProperty("checkoutPath");
});


test("production rendering omits local checkout mode rather than disabling it", () => {
  render(
    <RepositoryCollectionForm onSubmit={() => {}} pending={false} allowLocalCheckout={false} />,
  );

  const options = screen
    .getAllByRole("option")
    .map((option) => (option as HTMLOptionElement).value);
  expect(options).toEqual(["GIT"]);
  expect(screen.queryByLabelText("Development checkout path")).toBeNull();
});


test("development mode offers the checkout path only for the local source", async () => {
  const user = userEvent.setup();
  const submissions: RepositoryCollectionRequest[] = [];
  render(
    <RepositoryCollectionForm
      onSubmit={(body) => submissions.push(body)}
      pending={false}
      allowLocalCheckout
    />,
  );

  expect(screen.queryByLabelText("Development checkout path")).toBeNull();
  await user.selectOptions(screen.getByLabelText("Source mode"), "LOCAL_CHECKOUT");
  await fillGitForm(user);
  await user.type(
    screen.getByLabelText("Development checkout path"),
    "/work/spring-petclinic",
  );
  await user.click(screen.getByRole("button", { name: "Collect repository" }));

  expect(submissions[0].sourceType).toBe("LOCAL_CHECKOUT");
  expect(submissions[0].checkoutPath).toBe("/work/spring-petclinic");
});


test("rejects a revision that is not an exact commit and keeps the typed values", async () => {
  const user = userEvent.setup();
  const submissions: RepositoryCollectionRequest[] = [];
  render(
    <RepositoryCollectionForm
      onSubmit={(body) => submissions.push(body)}
      pending={false}
      allowLocalCheckout={false}
    />,
  );

  await user.type(
    screen.getByLabelText("Repository origin (HTTPS)"),
    "https://github.com/acme/demo",
  );
  await user.type(screen.getByLabelText("Repository name"), "demo");
  await user.type(screen.getByLabelText("Exact commit (40 hex)"), "main");
  await user.type(screen.getByLabelText("System"), "payments");
  await user.click(screen.getByRole("button", { name: "Collect repository" }));

  expect(submissions).toHaveLength(0);
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Revision must be an exact lowercase 40-character commit.",
  );
  expect(screen.getByLabelText("Repository name")).toHaveValue("demo");
});


test("cannot submit twice while a submission is pending", async () => {
  const user = userEvent.setup();
  const submissions: RepositoryCollectionRequest[] = [];
  render(
    <RepositoryCollectionForm
      onSubmit={(body) => submissions.push(body)}
      pending
      allowLocalCheckout={false}
    />,
  );

  const submit = screen.getByRole("button", { name: "Submitting collection…" });
  expect(submit).toBeDisabled();
  await user.click(submit);
  expect(submissions).toHaveLength(0);
});


test("renders a bounded server failure with its code and correlation id", () => {
  render(
    <RepositoryCollectionForm
      onSubmit={() => {}}
      pending={false}
      allowLocalCheckout={false}
      error={
        new ApiError({
          code: "SOURCE_ACQUISITION_FAILED",
          message: "repository source acquisition failed",
          correlationId: "corr-77",
        })
      }
    />,
  );

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("SOURCE_ACQUISITION_FAILED");
  expect(alert).toHaveTextContent("corr-77");
});
