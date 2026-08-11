export type RuntimeEnvironment =
  | "local"
  | "fixture"
  | "ephemeral"
  | "production";

export interface RuntimeConfig {
  readonly schemaVersion: "1.0.0";
  readonly environment: RuntimeEnvironment;
  readonly apiBasePath: "/api";
  readonly sourceRevision: string;
  readonly demoActions: boolean;
}

type RuntimeScope = {
  readonly __LINEAGE_RUNTIME_CONFIG__?: unknown;
  readonly location?: { readonly hostname?: string };
};

const CONFIG_FIELDS = new Set([
  "schemaVersion",
  "environment",
  "apiBasePath",
  "sourceRevision",
  "demoActions",
]);
const ENVIRONMENTS = new Set<RuntimeEnvironment>([
  "local",
  "fixture",
  "ephemeral",
  "production",
]);
const LOCAL_CONFIG: RuntimeConfig = Object.freeze({
  schemaVersion: "1.0.0",
  environment: "local",
  apiBasePath: "/api",
  sourceRevision: "local-dev",
  demoActions: true,
});


export function parseRuntimeConfig(value: unknown): RuntimeConfig {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Lineage runtime configuration must be an object");
  }
  const record = value as Record<string, unknown>;
  const unknown = Object.keys(record).find((key) => !CONFIG_FIELDS.has(key));
  if (unknown) {
    throw new Error(`Lineage runtime configuration has unknown field: ${unknown}`);
  }
  if (record.schemaVersion !== "1.0.0") {
    throw new Error("Lineage runtime configuration version is unsupported");
  }
  if (
    typeof record.environment !== "string" ||
    !ENVIRONMENTS.has(record.environment as RuntimeEnvironment)
  ) {
    throw new Error("Lineage runtime environment is invalid");
  }
  if (record.apiBasePath !== "/api") {
    throw new Error("Lineage API base must be the same-origin /api path");
  }
  if (
    typeof record.sourceRevision !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:@/+\-=]{0,127}$/.test(record.sourceRevision)
  ) {
    throw new Error("Lineage source revision is invalid");
  }
  if (typeof record.demoActions !== "boolean") {
    throw new Error("Lineage demo action setting is invalid");
  }
  if (record.demoActions && record.environment !== "local") {
    throw new Error("Lineage demo actions are restricted to local development");
  }
  return Object.freeze({
    schemaVersion: "1.0.0",
    environment: record.environment as RuntimeEnvironment,
    apiBasePath: "/api",
    sourceRevision: record.sourceRevision,
    demoActions: record.demoActions,
  });
}


export function readRuntimeConfig(
  scope: RuntimeScope = globalThis as RuntimeScope,
  allowLocalFallback: boolean = isLoopback(scope.location?.hostname),
): RuntimeConfig {
  if (scope.__LINEAGE_RUNTIME_CONFIG__ !== undefined) {
    return parseRuntimeConfig(scope.__LINEAGE_RUNTIME_CONFIG__);
  }
  if (allowLocalFallback) return LOCAL_CONFIG;
  throw new Error("Lineage runtime configuration is unavailable");
}


function isLoopback(hostname: string | undefined): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}


export function apiPath(path: string, config = readRuntimeConfig()): string {
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("://")) {
    throw new Error("A relative API path is required");
  }
  if (path === "/" || path.startsWith("/api/") || path.includes("\\")) {
    throw new Error("A relative API resource path is required");
  }
  return `${config.apiBasePath}${path}`;
}
