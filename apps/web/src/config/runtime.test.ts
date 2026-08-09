import {
  apiPath,
  parseRuntimeConfig,
  readRuntimeConfig,
} from "./runtime";


const productionConfig = {
  schemaVersion: "1.0.0",
  environment: "production",
  apiBasePath: "/api",
  sourceRevision: "abc123",
  demoActions: false,
};


test("accepts only closed public same-origin runtime configuration", () => {
  const config = parseRuntimeConfig(productionConfig);

  expect(config).toEqual(productionConfig);
  expect(apiPath("/runs", config)).toBe("/api/runs");
  expect(() => apiPath("https://attacker.example/runs", config)).toThrow(
    /relative API path/,
  );
  expect(() =>
    parseRuntimeConfig({ ...productionConfig, apiBasePath: "https://api.example" }),
  ).toThrow(/same-origin/);
  expect(() =>
    parseRuntimeConfig({ ...productionConfig, secret: "must-not-ship" }),
  ).toThrow(/unknown field/);
});


test("demo actions cannot be enabled by deployed environments", () => {
  expect(() =>
    parseRuntimeConfig({ ...productionConfig, demoActions: true }),
  ).toThrow(/demo actions/);
  expect(
    parseRuntimeConfig({
      ...productionConfig,
      environment: "local",
      sourceRevision: "local-dev",
      demoActions: true,
    }).demoActions,
  ).toBe(true);
});


test("a production build fails closed when runtime configuration is absent", () => {
  expect(() => readRuntimeConfig({}, false)).toThrow(/runtime configuration/);
  expect(readRuntimeConfig({}, true).environment).toBe("local");
});
