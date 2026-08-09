import { Node } from "constructs";
import { Fn, aws_logs as logs } from "aws-cdk-lib";

export interface PlatformConfig {
  readonly environment: "fixture" | "ephemeral" | "production";
  readonly resourcePrefix: string;
  readonly account?: string;
  readonly primaryRegion?: string;
  readonly secondaryRegion?: string;
  readonly primaryAvailabilityZones?: readonly string[];
  readonly truthRetentionDays: number;
  readonly archiveRetentionDays: number;
  readonly monthlyBudgetUsd: number;
  readonly baselineMapConcurrency: number;
  readonly lambdaReservedConcurrency: Readonly<Record<string, number>>;
  readonly enterpriseEndpoint?: string;
  readonly enterpriseEndpointServiceName?: string;
  readonly lambdaImageDigest?: string;
  readonly scaImageDigest?: string;
  readonly pagingTopicArn?: string;
  readonly sourceRevision: string;
  readonly logRetention: logs.RetentionDays;
}

export function fixtureConfig(): PlatformConfig {
  return {
    environment: "fixture",
    resourcePrefix: "lineage-fixture",
    truthRetentionDays: 30,
    archiveRetentionDays: 7,
    monthlyBudgetUsd: 25,
    baselineMapConcurrency: 4,
    lambdaReservedConcurrency: {
      intake: 20,
      "control-stage": 30,
      classification: 20,
      "runtime-validation": 20,
      consolidation: 10,
      coverage: 15,
      proposal: 10,
      publication: 5,
      deployment: 2,
      "product-api": 20,
    },
    sourceRevision: "local-synth",
    logRetention: logs.RetentionDays.ONE_WEEK,
    primaryAvailabilityZones: [0, 1, 2].map((index) => Fn.select(index, Fn.getAzs())),
    secondaryRegion: "us-west-2",
    enterpriseEndpoint: "https://fixture.invalid",
    enterpriseEndpointServiceName:
      "com.amazonaws.vpce.us-east-1.vpce-svc-0123456789abcdef0",
    pagingTopicArn: "arn:aws:sns:us-east-1:111111111111:lineage-fixture-paging",
  };
}

function requiredContext(node: Node, name: string): string {
  const value = node.tryGetContext(name);
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`Missing mandatory production context: ${name}`);
  }
  return value;
}

function requiredPositiveNumber(node: Node, name: string): number {
  const value = Number(node.tryGetContext(name));
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`Missing mandatory production context: ${name}`);
  }
  return value;
}

function requiredBoundedInteger(node: Node, name: string, maximum: number): number {
  const value = Number(node.tryGetContext(name));
  if (!Number.isInteger(value) || value <= 0 || value > maximum) {
    throw new Error(
      `Invalid production context: ${name} must be an integer between 1 and ${maximum}`,
    );
  }
  return value;
}

function requiredSha256(node: Node, name: string): string {
  const value = requiredContext(node, name);
  if (!/^sha256:[0-9a-f]{64}$/.test(value)) {
    throw new Error(`Invalid production context: ${name} must be an immutable sha256 digest`);
  }
  return value;
}

function requiredAvailabilityZones(node: Node, region: string): string[] {
  const zones = requiredContext(node, "primaryAvailabilityZones")
    .split(",")
    .map((zone) => zone.trim())
    .filter(Boolean);
  if (zones.length < 3 || zones.some((zone) => !zone.startsWith(region))) {
    throw new Error("Production context primaryAvailabilityZones requires three zones in primaryRegion");
  }
  return zones;
}

const COMPUTE_TARGETS = [
  "intake",
  "control-stage",
  "classification",
  "runtime-validation",
  "consolidation",
  "coverage",
  "proposal",
  "publication",
  "deployment",
  "product-api",
] as const;

function requiredConcurrency(node: Node): Record<string, number> {
  const entries = requiredContext(node, "lambdaReservedConcurrency").split(",");
  const parsed: Record<string, number> = {};
  for (const entry of entries) {
    const [name, rawValue, ...extra] = entry.split("=");
    const value = Number(rawValue);
    if (extra.length || !COMPUTE_TARGETS.includes(name as (typeof COMPUTE_TARGETS)[number]) || !Number.isInteger(value) || value <= 0) {
      throw new Error("Invalid production context: lambdaReservedConcurrency");
    }
    parsed[name] = value;
  }
  if (Object.keys(parsed).length !== COMPUTE_TARGETS.length) {
    throw new Error("Invalid production context: lambdaReservedConcurrency requires every target");
  }
  if (parsed.intake < 6) {
    throw new Error(
      "Invalid production context: intake concurrency must be at least 6 to preserve lane headroom",
    );
  }
  return parsed;
}

export function loadPlatformConfig(node: Node): PlatformConfig {
  const environment = node.tryGetContext("environment") ?? "fixture";
  if (environment === "fixture") return fixtureConfig();
  if (environment !== "production" && environment !== "ephemeral") {
    throw new Error(`Unsupported environment context: ${String(environment)}`);
  }
  const primaryRegion = requiredContext(node, "primaryRegion");
  const resourcePrefix = requiredContext(node, "resourcePrefix");
  if (environment === "ephemeral" && !/^lineage-e2e-[a-z0-9][a-z0-9-]{2,32}$/.test(resourcePrefix)) {
    throw new Error("Ephemeral resourcePrefix must be a scoped lineage-e2e-* namespace");
  }
  return {
    environment,
    resourcePrefix,
    account: requiredContext(node, "account"),
    primaryRegion,
    secondaryRegion: requiredContext(node, "secondaryRegion"),
    primaryAvailabilityZones: requiredAvailabilityZones(node, primaryRegion),
    truthRetentionDays: requiredPositiveNumber(node, "truthRetentionDays"),
    archiveRetentionDays: requiredPositiveNumber(node, "archiveRetentionDays"),
    monthlyBudgetUsd: requiredPositiveNumber(node, "monthlyBudgetUsd"),
    baselineMapConcurrency: requiredBoundedInteger(node, "baselineMapConcurrency", 10_000),
    lambdaReservedConcurrency: requiredConcurrency(node),
    enterpriseEndpoint: requiredContext(node, "enterpriseEndpoint"),
    enterpriseEndpointServiceName: requiredContext(node, "enterpriseEndpointServiceName"),
    lambdaImageDigest: requiredSha256(node, "lambdaImageDigest"),
    scaImageDigest: requiredSha256(node, "scaImageDigest"),
    pagingTopicArn: requiredContext(node, "pagingTopicArn"),
    sourceRevision: requiredContext(node, "sourceRevision"),
    logRetention:
      environment === "production" ? logs.RetentionDays.THREE_MONTHS : logs.RetentionDays.ONE_WEEK,
  };
}

export function applyExplicitAwsContext(node: Node, config: PlatformConfig): void {
  if (
    config.environment === "fixture" ||
    !config.account ||
    !config.primaryRegion ||
    !config.primaryAvailabilityZones
  ) {
    return;
  }
  const key = `availability-zones:account=${config.account}:region=${config.primaryRegion}`;
  const existing = node.tryGetContext(key);
  if (existing !== undefined && JSON.stringify(existing) !== JSON.stringify(config.primaryAvailabilityZones)) {
    throw new Error(`Production context conflicts with explicit topology: ${key}`);
  }
  if (existing === undefined) node.setContext(key, [...config.primaryAvailabilityZones]);
}
