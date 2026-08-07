import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";

import {
  CfnOutput,
  Duration,
  IgnoreMode,
  Stack,
  aws_cloudwatch as cloudwatch,
  aws_cloudwatch_actions as cloudwatchActions,
  aws_codedeploy as codedeploy,
  aws_ecr as ecr,
  aws_ecr_assets as ecrAssets,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_logs as logs,
  aws_sns as sns,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";
import type { NetworkStack } from "./network-stack.js";

export const DOCKER_ASSET_EXCLUDES = [
  ".git",
  ".git/**",
  ".worktrees",
  ".worktrees/**",
  "node_modules",
  "node_modules/**",
  "apps/web",
  "apps/web/**",
  "apps/api/.venv",
  "apps/api/.venv/**",
  "apps/api/tests",
  "apps/api/tests/**",
  "data",
  "data/**",
  "docs",
  "docs/**",
  "fixtures",
  "fixtures/**",
  "infra/bin",
  "infra/bin/**",
  "infra/cdk.out",
  "infra/cdk.out/**",
  "infra/dist",
  "infra/dist/**",
  "infra/lib",
  "infra/lib/**",
  "infra/test",
  "infra/test/**",
  "output",
  "output/**",
  "packages",
  "packages/**",
  "tests",
  "tests/**",
  "**/__pycache__",
  "**/*.pyc",
] as const;

export interface LambdaTargetDefinition {
  readonly name: string;
  readonly handler: string;
  readonly memoryMiB: number;
  readonly timeoutSeconds: number;
  readonly reservedConcurrency: number;
}

export const LAMBDA_TARGETS: readonly LambdaTargetDefinition[] = [
  { name: "intake", handler: "lineage_api.entrypoints.aws.intake.handler", memoryMiB: 512, timeoutSeconds: 30, reservedConcurrency: 20 },
  { name: "control-stage", handler: "lineage_api.entrypoints.aws.control_stage.handler", memoryMiB: 512, timeoutSeconds: 60, reservedConcurrency: 30 },
  { name: "runtime-validation", handler: "lineage_api.entrypoints.aws.runtime_validation.handler", memoryMiB: 1024, timeoutSeconds: 60, reservedConcurrency: 20 },
  { name: "consolidation", handler: "lineage_api.entrypoints.aws.consolidation.handler", memoryMiB: 1536, timeoutSeconds: 180, reservedConcurrency: 10 },
  { name: "coverage", handler: "lineage_api.entrypoints.aws.coverage.handler", memoryMiB: 768, timeoutSeconds: 90, reservedConcurrency: 15 },
  { name: "proposal", handler: "lineage_api.entrypoints.aws.proposal.handler", memoryMiB: 768, timeoutSeconds: 90, reservedConcurrency: 10 },
  { name: "publication", handler: "lineage_api.entrypoints.aws.publication.handler", memoryMiB: 1536, timeoutSeconds: 300, reservedConcurrency: 5 },
  { name: "deployment", handler: "lineage_api.entrypoints.aws.deployment.handler", memoryMiB: 1024, timeoutSeconds: 300, reservedConcurrency: 2 },
] as const;

export function lambdaTarget(name: string): LambdaTargetDefinition {
  const target = LAMBDA_TARGETS.find((candidate) => candidate.name === name);
  if (!target) throw new Error(`Unknown Lambda target: ${name}`);
  return target;
}

export interface BuildMetadataInput {
  readonly sourceRevision: string;
  readonly dependencyLockDigest: string;
  readonly imageAssetDigest: string;
  readonly images: Readonly<Record<"lambda" | "sca", PackagedImageMetadata>>;
}

export interface PackagedImageMetadata {
  readonly platform: "linux/amd64" | "linux/arm64";
  readonly digest: string;
  readonly archiveDigest: string;
}

export function createBuildMetadata(input: BuildMetadataInput) {
  return {
    schemaVersion: "1.0.0",
    sourceRevision: input.sourceRevision,
    dependencyLockDigest: input.dependencyLockDigest,
    imageAssetDigest: input.imageAssetDigest,
    images: input.images,
    cdkContext: {
      lambdaImageDigest: input.images.lambda.digest,
      scaImageDigest: input.images.sca.digest,
    },
    handlers: LAMBDA_TARGETS.map(({ name, handler }) => ({ name, handler })),
    scaEntrypoint: "lineage_api.entrypoints.sca_worker",
  };
}

export interface RuntimeTargetProps {
  readonly config: PlatformConfig;
  readonly network: NetworkStack;
  readonly imageRepository: ecr.IRepository;
  readonly target: LambdaTargetDefinition;
  readonly environment: Record<string, string>;
  readonly policyStatements?: readonly iam.PolicyStatement[];
}

export class RuntimeTarget extends Construct {
  readonly function: lambda.DockerImageFunction;
  readonly alias: lambda.Alias;
  readonly alarm: cloudwatch.Alarm;
  readonly deploymentGroup: codedeploy.LambdaDeploymentGroup;
  readonly role: iam.Role;

  constructor(scope: Construct, id: string, props: RuntimeTargetProps) {
    super(scope, id);
    const repoRoot = resolve(import.meta.dirname, "../..");
    const logGroup = new logs.LogGroup(this, "Logs", {
      logGroupName: `/aws/lambda/${props.config.resourcePrefix}-${props.target.name}`,
      retention: props.config.logRetention,
    });
    this.role = new iam.Role(this, "Role", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      description: `Scoped execution role for ${props.target.name}`,
    });
    this.role.addToPolicy(
      new iam.PolicyStatement({
        actions: ["logs:CreateLogStream", "logs:PutLogEvents"],
        resources: [`${logGroup.logGroupArn}:*`],
      }),
    );
    this.role.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "ec2:AssignPrivateIpAddresses",
          "ec2:CreateNetworkInterface",
          "ec2:DeleteNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:UnassignPrivateIpAddresses",
        ],
        resources: ["*"],
      }),
    );
    for (const statement of props.policyStatements ?? []) this.role.addToPolicy(statement);

    let image: ecrAssets.DockerImageAsset | undefined;
    let imageDigest: string;
    let code: lambda.DockerImageCode;
    if (props.config.environment === "production") {
      if (!props.config.lambdaImageDigest) {
        throw new Error("Production Lambda image digest is required");
      }
      imageDigest = props.config.lambdaImageDigest;
      code = lambda.DockerImageCode.fromEcr(props.imageRepository, {
        tagOrDigest: imageDigest,
        cmd: [props.target.handler],
      });
    } else {
      image = new ecrAssets.DockerImageAsset(this, "Image", {
        directory: repoRoot,
        file: "infra/assets/lambda/Dockerfile",
        platform: ecrAssets.Platform.LINUX_AMD64,
        ignoreMode: IgnoreMode.GLOB,
        exclude: [...DOCKER_ASSET_EXCLUDES],
      });
      imageDigest = image.assetHash;
      code = lambda.DockerImageCode.fromEcr(image.repository, {
        tagOrDigest: image.imageTag,
        cmd: [props.target.handler],
      });
    }
    this.function = new lambda.DockerImageFunction(this, "Function", {
      functionName: `${props.config.resourcePrefix}-${props.target.name}`,
      description: `${props.target.name} lineage stage (${props.target.handler})`,
      code,
      role: this.role,
      vpc: props.network.vpc,
      vpcSubnets: { subnets: props.network.applicationSubnets },
      securityGroups: [props.network.runtimeSecurityGroup],
      memorySize: props.target.memoryMiB,
      timeout: Duration.seconds(props.target.timeoutSeconds),
      reservedConcurrentExecutions:
        props.config.lambdaReservedConcurrency[props.target.name] ?? props.target.reservedConcurrency,
      environment: {
        ...props.environment,
        LINEAGE_ENVIRONMENT: props.config.environment,
        LINEAGE_HANDLER: props.target.handler,
        LINEAGE_SOURCE_REVISION: props.config.sourceRevision,
        LINEAGE_ENTERPRISE_ENDPOINT: props.config.enterpriseEndpoint ?? "fixture-only",
        LINEAGE_SECONDARY_REGION: props.config.secondaryRegion ?? "fixture-only",
      },
      logGroup,
      tracing: lambda.Tracing.ACTIVE,
      architecture: lambda.Architecture.X86_64,
    });
    const version = this.function.currentVersion;
    this.alias = new lambda.Alias(this, "LiveAlias", {
      aliasName: "live",
      version,
    });
    this.alarm = new cloudwatch.Alarm(this, "ErrorsAlarm", {
      metric: this.function.metricErrors({ period: Duration.minutes(1) }),
      threshold: 1,
      evaluationPeriods: 1,
      alarmDescription: `${props.target.name} failed invocations`,
    });
    if (props.config.pagingTopicArn) {
      const pagingTopic = sns.Topic.fromTopicArn(this, "PagingTopic", props.config.pagingTopicArn);
      this.alarm.addAlarmAction(new cloudwatchActions.SnsAction(pagingTopic));
    }
    this.deploymentGroup = new codedeploy.LambdaDeploymentGroup(this, "CanaryDeployment", {
      alias: this.alias,
      deploymentGroupName: `${props.config.resourcePrefix}-${props.target.name}`,
      deploymentConfig: codedeploy.LambdaDeploymentConfig.CANARY_10PERCENT_10MINUTES,
      alarms: [this.alarm],
      autoRollback: {
        failedDeployment: true,
        stoppedDeployment: true,
        deploymentInAlarm: true,
      },
    });
    new CfnOutput(Stack.of(this), `${id}AliasArn`, { value: this.alias.functionArn });
    new CfnOutput(Stack.of(this), `${id}FunctionName`, { value: this.function.functionName });
    new CfnOutput(Stack.of(this), `${id}FunctionVersionArn`, { value: version.functionArn });
    new CfnOutput(Stack.of(this), `${id}RoleArn`, { value: this.role.roleArn });
    new CfnOutput(Stack.of(this), `${id}DeploymentGroupName`, {
      value: this.deploymentGroup.deploymentGroupName,
    });
    new CfnOutput(Stack.of(this), `${id}Handler`, { value: props.target.handler });
    new CfnOutput(Stack.of(this), `${id}ImageDigest`, { value: imageDigest });
  }
}

function digestRuntimeInputs(repoRoot: string): string {
  const hash = createHash("sha256");
  function visit(current: string): void {
    for (const name of readdirSync(current).sort()) {
      if (["__pycache__", ".pytest_cache", ".venv"].includes(name)) continue;
      const child = join(current, name);
      const stat = statSync(child);
      if (stat.isDirectory()) visit(child);
      else {
        hash.update(child.slice(repoRoot.length));
        hash.update(readFileSync(child));
      }
    }
  }
  visit(resolve(repoRoot, "apps/api/src"));
  for (const relative of [
    ".dockerignore",
    "apps/api/pyproject.toml",
    "apps/api/uv.lock",
    "infra/assets/lambda/Dockerfile",
    "infra/assets/sca/Dockerfile",
  ]) {
    hash.update(relative);
    hash.update(readFileSync(resolve(repoRoot, relative)));
  }
  return `sha256:${hash.digest("hex")}`;
}

function digestFile(path: string): string {
  return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

function requireSha256(value: unknown, field: string): string {
  if (typeof value !== "string" || !/^sha256:[0-9a-f]{64}$/.test(value)) {
    throw new Error(`Docker did not emit a valid ${field}`);
  }
  return value;
}

function buildOciImage(
  repoRoot: string,
  output: string,
  name: "lambda" | "sca",
  platform: PackagedImageMetadata["platform"],
  dockerfile: string,
  sourceDateEpoch: string,
): PackagedImageMetadata {
  const archive = resolve(output, `${name}.oci.tar`);
  const metadataPath = resolve(output, `${name}.build.json`);
  execFileSync(
    "docker",
    [
      "buildx",
      "build",
      "--platform",
      platform,
      "--file",
      resolve(repoRoot, dockerfile),
      "--output",
      `type=oci,dest=${archive}`,
      "--metadata-file",
      metadataPath,
      "--build-arg",
      `SOURCE_DATE_EPOCH=${sourceDateEpoch}`,
      "--provenance=false",
      "--sbom=false",
      repoRoot,
    ],
    { stdio: "inherit" },
  );
  const buildMetadata = JSON.parse(readFileSync(metadataPath, "utf8")) as Record<string, any>;
  const digest = requireSha256(
    buildMetadata["containerimage.digest"] ??
      buildMetadata["containerimage.descriptor"]?.digest,
    `${name} image digest`,
  );
  return {
    platform,
    digest,
    archiveDigest: requireSha256(digestFile(archive), `${name} OCI archive digest`),
  };
}

export function packageRuntimeAssets(): void {
  const infraRoot = resolve(import.meta.dirname, "..");
  const repoRoot = resolve(infraRoot, "..");
  const output = resolve(infraRoot, "dist");
  const wheels = resolve(output, "wheels");
  if (existsSync(output)) rmSync(output, { recursive: true });
  mkdirSync(wheels, { recursive: true });
  execFileSync(
    "uv",
    ["build", "--project", resolve(repoRoot, "apps/api"), "--wheel", "--out-dir", wheels],
    { stdio: "inherit" },
  );
  const lock = readFileSync(resolve(repoRoot, "apps/api/uv.lock"));
  const sourceRevision = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: repoRoot,
    encoding: "utf8",
  }).trim();
  const sourceDateEpoch = execFileSync("git", ["show", "-s", "--format=%ct", sourceRevision], {
    cwd: repoRoot,
    encoding: "utf8",
  }).trim();
  const imageOutput = resolve(output, "images");
  mkdirSync(imageOutput, { recursive: true });
  const images = {
    lambda: buildOciImage(
      repoRoot,
      imageOutput,
      "lambda",
      "linux/amd64",
      "infra/assets/lambda/Dockerfile",
      sourceDateEpoch,
    ),
    sca: buildOciImage(
      repoRoot,
      imageOutput,
      "sca",
      "linux/arm64",
      "infra/assets/sca/Dockerfile",
      sourceDateEpoch,
    ),
  } as const;
  const metadata = createBuildMetadata({
    sourceRevision,
    dependencyLockDigest: `sha256:${createHash("sha256").update(lock).digest("hex")}`,
    imageAssetDigest: digestRuntimeInputs(repoRoot),
    images,
  });
  writeFileSync(resolve(output, "runtime-build-metadata.json"), `${JSON.stringify(metadata, null, 2)}\n`);
  const wheelNames = readdirSync(wheels).sort();
  writeFileSync(
    resolve(output, "package-manifest.json"),
    `${JSON.stringify({ ...metadata, wheelNames }, null, 2)}\n`,
  );
}

if (process.argv[2] === "package") packageRuntimeAssets();
