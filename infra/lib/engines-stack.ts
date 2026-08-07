import { resolve } from "node:path";

import {
  CfnOutput,
  Duration,
  IgnoreMode,
  Stack,
  type StackProps,
  aws_ecs as ecs,
  aws_ecr_assets as ecrAssets,
  aws_cloudwatch as cloudwatch,
  aws_cloudwatch_actions as cloudwatchActions,
  aws_iam as iam,
  aws_logs as logs,
  aws_sns as sns,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";
import type { DataStack } from "./data-stack.js";
import type { NetworkStack } from "./network-stack.js";
import { DOCKER_ASSET_EXCLUDES, RuntimeTarget, lambdaTarget } from "./runtime-assets.js";

export interface EnginesStackProps extends StackProps {
  readonly config: PlatformConfig;
  readonly network: NetworkStack;
  readonly data: DataStack;
}

export class EnginesStack extends Stack {
  readonly consolidation: RuntimeTarget;
  readonly proposal: RuntimeTarget;
  readonly cluster: ecs.Cluster;
  readonly scaTask: ecs.FargateTaskDefinition;

  constructor(scope: Construct, id: string, props: EnginesStackProps) {
    super(scope, id, props);
    this.consolidation = new RuntimeTarget(this, "ConsolidationTarget", {
      config: props.config,
      network: props.network,
      imageRepository: props.data.lambdaImageRepository,
      target: lambdaTarget("consolidation"),
      environment: props.data.runtimeEnvironment(),
      policyStatements: props.data.dataPlaneStatements("consolidation"),
    });
    this.proposal = new RuntimeTarget(this, "ProposalTarget", {
      config: props.config,
      network: props.network,
      imageRepository: props.data.lambdaImageRepository,
      target: lambdaTarget("proposal"),
      environment: props.data.runtimeEnvironment(),
      policyStatements: props.data.dataPlaneStatements("proposal"),
    });
    this.cluster = new ecs.Cluster(this, "ScaCluster", {
      vpc: props.network.vpc,
      clusterName: `${props.config.resourcePrefix}-sca`,
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
    });
    const taskRole = new iam.Role(this, "ScaTaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      description: "Scoped SCA evidence worker role",
    });
    for (const statement of props.data.dataPlaneStatements("sca")) taskRole.addToPolicy(statement);
    this.scaTask = new ecs.FargateTaskDefinition(this, "ScaTask", {
      cpu: 2048,
      memoryLimitMiB: 4096,
      taskRole,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.ARM64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });
    const scaLogs = new logs.LogGroup(this, "ScaLogs", { retention: props.config.logRetention });
    const scaFailureMetric = new logs.MetricFilter(this, "ScaFailureMetric", {
      logGroup: scaLogs,
      filterPattern: logs.FilterPattern.anyTerm("ERROR", "FAILED", "FATAL"),
      metricNamespace: "LineageCollector",
      metricName: `${props.config.resourcePrefix}-sca-failures`,
      metricValue: "1",
      defaultValue: 0,
    });
    const scaAlarm = new cloudwatch.Alarm(this, "ScaFailureAlarm", {
      metric: scaFailureMetric.metric({ period: Duration.minutes(1) }),
      threshold: 1,
      evaluationPeriods: 1,
      alarmDescription: "SCA Fargate worker emitted a terminal failure",
    });
    if (props.config.pagingTopicArn) {
      scaAlarm.addAlarmAction(
        new cloudwatchActions.SnsAction(
          sns.Topic.fromTopicArn(this, "PagingTopic", props.config.pagingTopicArn),
        ),
      );
    }
    const scaImage = props.config.environment === "production"
      ? (() => {
          if (!props.config.scaImageDigest) {
            throw new Error("Production SCA image digest is required");
          }
          return ecs.ContainerImage.fromEcrRepository(
            props.data.scaImageRepository,
            props.config.scaImageDigest,
          );
        })()
      : ecs.ContainerImage.fromAsset(resolve(import.meta.dirname, "../.."), {
          file: "infra/assets/sca/Dockerfile",
          platform: ecrAssets.Platform.LINUX_ARM64,
          ignoreMode: IgnoreMode.GLOB,
          exclude: [...DOCKER_ASSET_EXCLUDES],
        });
    this.scaTask.addContainer("ScaContainer", {
      containerName: "lineage-sca",
      image: scaImage,
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: "sca", logGroup: scaLogs }),
      environment: {
        ...props.data.runtimeEnvironment(),
        LINEAGE_ENTERPRISE_ENDPOINT: props.config.enterpriseEndpoint ?? "fixture-only",
        LINEAGE_SECONDARY_REGION: props.config.secondaryRegion ?? "fixture-only",
      },
      readonlyRootFilesystem: true,
      user: "lineage",
    });
    new CfnOutput(this, "ScaTaskDefinitionArn", { value: this.scaTask.taskDefinitionArn });
    new CfnOutput(this, "ScaClusterArn", { value: this.cluster.clusterArn });
  }
}
