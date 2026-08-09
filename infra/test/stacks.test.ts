import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";

import { applyExplicitAwsContext, fixtureConfig, loadPlatformConfig } from "../lib/config.js";
import { ApiStack } from "../lib/api-stack.js";
import { DataStack } from "../lib/data-stack.js";
import { EnginesStack } from "../lib/engines-stack.js";
import { IntakeStack } from "../lib/intake-stack.js";
import { NetworkStack } from "../lib/network-stack.js";
import { OperationsStack } from "../lib/operations-stack.js";
import { OrchestrationStack } from "../lib/orchestration-stack.js";
import { PublicationStack } from "../lib/publication-stack.js";
import { RecoveryStack } from "../lib/recovery-stack.js";
import { RuntimeStack } from "../lib/runtime-stack.js";

function platform() {
  const app = new App();
  const config = fixtureConfig();
  const recovery = new RecoveryStack(app, "Recovery", { config });
  const network = new NetworkStack(app, "Network", { config });
  const data = new DataStack(app, "Data", { config, network, recovery });
  const engines = new EnginesStack(app, "Engines", { config, network, data });
  const runtime = new RuntimeStack(app, "Runtime", { config, network, data });
  const publication = new PublicationStack(app, "Publication", { config, network, data });
  const orchestration = new OrchestrationStack(app, "Orchestration", {
    config,
    network,
    data,
    engines,
    runtime,
    publication,
  });
  const intake = new IntakeStack(app, "Intake", { config, network, data, orchestration });
  const api = new ApiStack(app, "Api", { config, network, data });
  const operations = new OperationsStack(app, "Operations", { config, network, data });
  return { app, recovery, network, data, intake, orchestration, engines, runtime, publication, api, operations };
}

describe("lineage platform stacks", () => {
  let stacks: ReturnType<typeof platform>;

  beforeAll(() => {
    stacks = platform();
  }, 30_000);

  it("fails closed when mandatory production context is absent", () => {
    const app = new App({ context: { environment: "production" } });
    expect(() => loadPlatformConfig(app.node)).toThrow(/production context/i);
  });

  it("requires explicit production compute quotas", () => {
    const app = new App({
      context: {
        environment: "production",
        resourcePrefix: "lineage-prod",
        account: "111111111111",
        primaryRegion: "us-east-1",
        secondaryRegion: "us-west-2",
        primaryAvailabilityZones: "us-east-1a,us-east-1b,us-east-1c",
        truthRetentionDays: "2555",
        archiveRetentionDays: "90",
        monthlyBudgetUsd: "1000",
        baselineMapConcurrency: "25",
        enterpriseEndpoint: "https://catalog.example.internal",
        pagingTopicArn: "arn:aws:sns:us-east-1:111111111111:lineage-paging",
        sourceRevision: "test",
      },
    });
    expect(() => loadPlatformConfig(app.node)).toThrow(/lambdaReservedConcurrency/);
  });

  it("bounds Baseline distributed-map concurrency to the Step Functions service limit", () => {
    const app = new App({
      context: {
        environment: "production",
        resourcePrefix: "lineage-prod",
        account: "111111111111",
        primaryRegion: "us-east-1",
        secondaryRegion: "us-west-2",
        primaryAvailabilityZones: "us-east-1a,us-east-1b,us-east-1c",
        truthRetentionDays: "2555",
        archiveRetentionDays: "90",
        monthlyBudgetUsd: "1000",
        baselineMapConcurrency: "10001",
        lambdaReservedConcurrency:
          "intake=20,control-stage=30,classification=20,runtime-validation=20,consolidation=10,coverage=15,proposal=10,publication=5,deployment=2,product-api=20",
        enterpriseEndpoint: "https://catalog.example.internal",
        enterpriseEndpointServiceName:
          "com.amazonaws.vpce.us-east-1.vpce-svc-0123456789abcdef0",
        lambdaImageDigest: `sha256:${"a".repeat(64)}`,
        scaImageDigest: `sha256:${"b".repeat(64)}`,
        pagingTopicArn: "arn:aws:sns:us-east-1:111111111111:lineage-paging",
        sourceRevision: "test",
      },
    });

    expect(() => loadPlatformConfig(app.node)).toThrow(/baselineMapConcurrency.*10000/);
  });

  it("allows only disposable namespaced AWS smoke environments", () => {
    const context = {
      environment: "ephemeral",
      resourcePrefix: "lineage-e2e-task19",
      account: "111111111111",
      primaryRegion: "us-east-1",
      secondaryRegion: "us-west-2",
      primaryAvailabilityZones: "us-east-1a,us-east-1b,us-east-1c",
      truthRetentionDays: "1",
      archiveRetentionDays: "1",
      monthlyBudgetUsd: "25",
      baselineMapConcurrency: "4",
      lambdaReservedConcurrency:
        "intake=6,control-stage=2,classification=2,runtime-validation=2,consolidation=2,coverage=2,proposal=2,publication=1,deployment=1,product-api=4",
      enterpriseEndpoint: "https://catalog.example.internal",
      enterpriseEndpointServiceName:
        "com.amazonaws.vpce.us-east-1.vpce-svc-0123456789abcdef0",
      lambdaImageDigest: `sha256:${"a".repeat(64)}`,
      scaImageDigest: `sha256:${"b".repeat(64)}`,
      pagingTopicArn: "arn:aws:sns:us-east-1:111111111111:lineage-paging",
      sourceRevision: "test",
    };
    const invalid = new App({ context: { ...context, resourcePrefix: "shared-dev" } });
    expect(() => loadPlatformConfig(invalid.node)).toThrow(/lineage-e2e/);

    const app = new App({ context });
    const config = loadPlatformConfig(app.node);
    applyExplicitAwsContext(app.node, config);
    const network = new NetworkStack(app, "EphemeralNetwork", { config });
    const recovery = new RecoveryStack(app, "EphemeralRecovery", { config });
    const data = new DataStack(app, "EphemeralData", { config, network, recovery });
    const template = Template.fromStack(data);

    template.allResourcesProperties("AWS::DynamoDB::Table", {
      DeletionProtectionEnabled: false,
    });
    template.hasResourceProperties("AWS::Neptune::DBCluster", {
      DeletionProtection: false,
    });
    template.allResources("AWS::S3::Bucket", {
      Properties: Match.not(Match.objectLike({ ObjectLockEnabled: true })),
      DeletionPolicy: "Delete",
    });
  });

  it("requires a declared private route to the enterprise endpoint", () => {
    const app = new App({
      context: {
        environment: "production",
        resourcePrefix: "lineage-prod",
        account: "111111111111",
        primaryRegion: "us-east-1",
        secondaryRegion: "us-west-2",
        primaryAvailabilityZones: "us-east-1a,us-east-1b,us-east-1c",
        truthRetentionDays: "2555",
        archiveRetentionDays: "90",
        monthlyBudgetUsd: "1000",
        baselineMapConcurrency: "25",
        lambdaReservedConcurrency:
          "intake=20,control-stage=30,classification=20,runtime-validation=20,consolidation=10,coverage=15,proposal=10,publication=5,deployment=2,product-api=20",
        enterpriseEndpoint: "https://catalog.example.internal",
        pagingTopicArn: "arn:aws:sns:us-east-1:111111111111:lineage-paging",
        sourceRevision: "test",
      },
    });
    expect(() => loadPlatformConfig(app.node)).toThrow(/enterpriseEndpointServiceName/);
  });

  it("requires exact immutable production image digests", () => {
    const app = new App({
      context: {
        environment: "production",
        resourcePrefix: "lineage-prod",
        account: "111111111111",
        primaryRegion: "us-east-1",
        secondaryRegion: "us-west-2",
        primaryAvailabilityZones: "us-east-1a,us-east-1b,us-east-1c",
        truthRetentionDays: "2555",
        archiveRetentionDays: "90",
        monthlyBudgetUsd: "1000",
        baselineMapConcurrency: "25",
        lambdaReservedConcurrency:
          "intake=20,control-stage=30,classification=20,runtime-validation=20,consolidation=10,coverage=15,proposal=10,publication=5,deployment=2,product-api=20",
        enterpriseEndpoint: "https://catalog.example.internal",
        enterpriseEndpointServiceName:
          "com.amazonaws.vpce.us-east-1.vpce-svc-0123456789abcdef0",
        pagingTopicArn: "arn:aws:sns:us-east-1:111111111111:lineage-paging",
        sourceRevision: "test",
      },
    });
    expect(() => loadPlatformConfig(app.node)).toThrow(/lambdaImageDigest/);
  });

  it("synthesizes explicit production topology without AWS context lookups", () => {
    const app = new App();
    const config = {
      ...fixtureConfig(),
      environment: "production" as const,
      resourcePrefix: "lineage-prod",
      account: "111111111111",
      primaryRegion: "us-east-1",
      secondaryRegion: "us-west-2",
      primaryAvailabilityZones: ["us-east-1a", "us-east-1b", "us-east-1c"],
      enterpriseEndpoint: "https://catalog.example.internal",
      enterpriseEndpointServiceName:
        "com.amazonaws.vpce.us-east-1.vpce-svc-0123456789abcdef0",
      lambdaImageDigest: `sha256:${"a".repeat(64)}`,
      scaImageDigest: `sha256:${"b".repeat(64)}`,
      pagingTopicArn: "arn:aws:sns:us-east-1:111111111111:lineage-paging",
    };
    applyExplicitAwsContext(app.node, config);
    const network = new NetworkStack(app, "ProductionNetwork", {
      config,
      env: { account: config.account, region: config.primaryRegion },
    });
    const recovery = new RecoveryStack(app, "ProductionRecovery", {
      config,
      env: { account: config.account, region: config.secondaryRegion },
      crossRegionReferences: true,
    });
    const data = new DataStack(app, "ProductionData", {
      config,
      network,
      recovery,
      env: { account: config.account, region: config.primaryRegion },
      crossRegionReferences: true,
    });
    const engines = new EnginesStack(app, "ProductionEngines", {
      config,
      network,
      data,
      env: { account: config.account, region: config.primaryRegion },
    });
    const runtime = new RuntimeStack(app, "ProductionRuntime", {
      config,
      network,
      data,
      env: { account: config.account, region: config.primaryRegion },
    });
    const publication = new PublicationStack(app, "ProductionPublication", {
      config,
      network,
      data,
      env: { account: config.account, region: config.primaryRegion },
    });
    const orchestration = new OrchestrationStack(app, "ProductionOrchestration", {
      config,
      network,
      data,
      engines,
      runtime,
      publication,
      env: { account: config.account, region: config.primaryRegion },
    });
    const intake = new IntakeStack(app, "ProductionIntake", {
      config,
      network,
      data,
      orchestration,
      env: { account: config.account, region: config.primaryRegion },
    });

    expect(app.synth().manifest.missing).toBeUndefined();
    expect(JSON.stringify(Template.fromStack(intake).toJSON())).toContain(
      config.lambdaImageDigest,
    );
    expect(JSON.stringify(Template.fromStack(engines).toJSON())).toContain(
      config.scaImageDigest,
    );
  });

  it("protects truth stores, control state, streams and the multi-AZ graph", () => {
    const template = Template.fromStack(stacks.data);
    template.resourceCountIs("AWS::S3::Bucket", 2);
    template.allResourcesProperties("AWS::S3::Bucket", {
      ReplicationConfiguration: Match.anyValue(),
    });
    template.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({ Action: Match.arrayWith(["s3:ReplicateObject"]) }),
        ]),
      },
    });
    template.allResourcesProperties("AWS::S3::Bucket", {
      BucketEncryption: Match.anyValue(),
      VersioningConfiguration: { Status: "Enabled" },
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
      ObjectLockEnabled: true,
      ObjectLockConfiguration: {
        ObjectLockEnabled: "Enabled",
        Rule: { DefaultRetention: { Days: 30, Mode: "GOVERNANCE" } },
      },
    });
    template.resourceCountIs("AWS::DynamoDB::Table", 4);
    template.resourceCountIs("AWS::ECR::Repository", 2);
    template.allResourcesProperties("AWS::ECR::Repository", {
      EncryptionConfiguration: { EncryptionType: "KMS", KmsKey: Match.anyValue() },
      ImageScanningConfiguration: { ScanOnPush: true },
      ImageTagMutability: "IMMUTABLE",
    });
    template.allResourcesProperties("AWS::DynamoDB::Table", {
      BillingMode: "PAY_PER_REQUEST",
      DeletionProtectionEnabled: true,
      PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
      SSESpecification: { SSEEnabled: true },
    });
    template.hasResourceProperties("AWS::Kinesis::Stream", {
      StreamEncryption: { EncryptionType: "KMS", KeyId: Match.anyValue() },
    });
    template.hasResourceProperties("AWS::Neptune::DBCluster", {
      DeletionProtection: true,
      IamAuthEnabled: true,
      StorageEncrypted: true,
    });
    template.resourceCountIs("AWS::Neptune::DBInstance", 3);
    const recovery = Template.fromStack(stacks.recovery);
    recovery.resourceCountIs("AWS::S3::Bucket", 2);
    recovery.allResourcesProperties("AWS::S3::Bucket", {
      ObjectLockEnabled: true,
      VersioningConfiguration: { Status: "Enabled" },
    });
  });

  it("creates three lanes with DLQs, archived events and reserved intake capacity", () => {
    const template = Template.fromStack(stacks.intake);
    template.resourceCountIs("AWS::SQS::Queue", 6);
    template.hasResourceProperties("AWS::SQS::Queue", { FifoQueue: true });
    template.hasResourceProperties("AWS::Events::Archive", {
      RetentionDays: Match.anyValue(),
    });
    template.hasResourceProperties("AWS::Lambda::Function", {
      PackageType: "Image",
      ReservedConcurrentExecutions: Match.anyValue(),
    });
  });

  it("gives private compute only the networking permissions and egress it requires", () => {
    const network = Template.fromStack(stacks.network);
    network.hasResourceProperties("AWS::EC2::VPCEndpoint", {
      PrivateDnsEnabled: true,
      ServiceName: "com.amazonaws.vpce.us-east-1.vpce-svc-0123456789abcdef0",
      VpcEndpointType: "Interface",
    });
    network.hasResourceProperties("AWS::EC2::SecurityGroupEgress", {
      IpProtocol: "tcp",
      FromPort: 443,
      ToPort: 443,
      DestinationSecurityGroupId: Match.anyValue(),
    });
    network.hasResourceProperties("AWS::EC2::SecurityGroupEgress", {
      IpProtocol: "tcp",
      FromPort: 8182,
      ToPort: 8182,
      DestinationSecurityGroupId: Match.anyValue(),
    });

    const templates = [
      stacks.intake,
      stacks.orchestration,
      stacks.engines,
      stacks.runtime,
      stacks.publication,
      stacks.operations,
      stacks.api,
    ].map((stack) => Template.fromStack(stack).toJSON());
    const eniPolicies = templates.flatMap((template) =>
      Object.values(template.Resources as Record<string, any>).filter(
        (resource: any) =>
          resource.Type === "AWS::IAM::Policy" &&
          JSON.stringify(resource.Properties.PolicyDocument.Statement).includes(
            "ec2:CreateNetworkInterface",
          ),
      ),
    );
    expect(eniPolicies).toHaveLength(10);

    const dynamoResourceScopes = eniPolicies.flatMap((resource: any) =>
      resource.Properties.PolicyDocument.Statement.filter((statement: any) =>
        JSON.stringify(statement.Action).includes("dynamodb:GetItem"),
      ).map((statement: any) => JSON.stringify(statement.Resource)),
    );
    expect(new Set(dynamoResourceScopes).size).toBeGreaterThanOrEqual(5);
  });

  it("creates four Standard workflows and ten distinct versioned Lambda targets", () => {
    const templates = [
      stacks.intake,
      stacks.orchestration,
      stacks.engines,
      stacks.runtime,
      stacks.publication,
      stacks.operations,
      stacks.api,
    ].map((stack) => Template.fromStack(stack));
    const resources = templates.flatMap((template) => Object.values(template.toJSON().Resources));
    const functions = resources.filter((resource: any) => resource.Type === "AWS::Lambda::Function");
    const alarms = resources.filter((resource: any) => resource.Type === "AWS::CloudWatch::Alarm");
    const roles = resources.filter((resource: any) => resource.Type === "AWS::IAM::Role");
    const aliases = resources.filter((resource: any) => resource.Type === "AWS::Lambda::Alias");
    const deploymentGroups = resources.filter(
      (resource: any) => resource.Type === "AWS::CodeDeploy::DeploymentGroup",
    );
    const stateMachines = resources.filter(
      (resource: any) => resource.Type === "AWS::StepFunctions::StateMachine",
    );
    expect(functions).toHaveLength(10);
    expect(alarms.length).toBeGreaterThanOrEqual(15);
    expect(aliases).toHaveLength(10);
    expect(deploymentGroups).toHaveLength(10);
    expect(
      deploymentGroups.every(
        (resource: any) =>
          resource.Properties.DeploymentConfigName === "CodeDeployDefault.LambdaCanary10Percent10Minutes",
      ),
    ).toBe(true);
    expect(roles.length).toBeGreaterThanOrEqual(10);
    expect(stateMachines).toHaveLength(4);
    expect(stateMachines.every((resource: any) => resource.Properties.StateMachineType === "STANDARD")).toBe(true);
    expect(new Set(functions.map((resource: any) => JSON.stringify(resource.Properties.ImageConfig))).size).toBe(10);
    expect(
      functions.every(
        (resource: any) =>
          resource.Properties.Environment.Variables.LINEAGE_ENTERPRISE_ENDPOINT ===
            "https://fixture.invalid" &&
          resource.Properties.Environment.Variables.LINEAGE_SECONDARY_REGION === "us-west-2",
      ),
    ).toBe(true);

    for (const resource of resources as any[]) {
      if (resource.Type !== "AWS::IAM::Policy") continue;
      const statements = resource.Properties.PolicyDocument.Statement;
      for (const statement of statements) {
        expect(statement.Action).not.toBe("*");
        expect(statement.Action).not.toContain("*");
      }
    }
  });

  it("packages SCA as a Fargate task and exposes product/operations resources", () => {
    Template.fromStack(stacks.engines).hasResourceProperties("AWS::ECS::TaskDefinition", {
      RequiresCompatibilities: ["FARGATE"],
      Volumes: [{ Name: "sca-scratch" }],
      ContainerDefinitions: Match.arrayWith([
        Match.objectLike({
          ReadonlyRootFilesystem: true,
          MountPoints: [
            {
              ContainerPath: "/opt/lineage-scratch",
              ReadOnly: false,
              SourceVolume: "sca-scratch",
            },
          ],
        }),
      ]),
    });
    Template.fromStack(stacks.api).resourceCountIs("AWS::ApiGateway::RestApi", 1);
    Template.fromStack(stacks.api).hasResourceProperties("AWS::Lambda::Function", {
      ImageConfig: { Command: ["lineage_api.entrypoints.aws.product_api.handler"] },
    });
    Template.fromStack(stacks.api).hasResourceProperties("AWS::ApiGateway::Method", {
      HttpMethod: "ANY",
      AuthorizationType: "AWS_IAM",
      Integration: Match.objectLike({ Type: "AWS_PROXY" }),
    });
    Template.fromStack(stacks.data).hasResourceProperties("AWS::DynamoDB::Table", {
      StreamSpecification: { StreamViewType: "NEW_IMAGE" },
      GlobalSecondaryIndexes: Match.arrayWith([
        Match.objectLike({ IndexName: "RunsByUpdatedAt" }),
      ]),
    });
    Template.fromStack(stacks.data).hasResourceProperties("AWS::DynamoDB::Table", {
      GlobalSecondaryIndexes: Match.arrayWith([
        Match.objectLike({ IndexName: "ProposalsByState" }),
      ]),
    });
    Template.fromStack(stacks.publication).hasResourceProperties(
      "AWS::Lambda::EventSourceMapping",
      {
        BatchSize: 10,
        FunctionResponseTypes: ["ReportBatchItemFailures"],
        FilterCriteria: Match.anyValue(),
      },
    );
    Template.fromStack(stacks.operations).hasResourceProperties("AWS::Budgets::Budget", {
      Budget: Match.anyValue(),
    });
  });

  it("grants the PR gate control and impact stages their exact pointer and graph reads", () => {
    const orchestration = JSON.stringify(Template.fromStack(stacks.orchestration).toJSON());

    expect(orchestration).toContain("neptune-db:Connect");
    expect(orchestration).toContain("PointerTable");
  });

  it("backs up control state and primary truth stores into a locked encrypted vault", () => {
    const template = Template.fromStack(stacks.operations);
    template.hasResourceProperties("AWS::Backup::BackupVault", {
      EncryptionKeyArn: Match.anyValue(),
      LockConfiguration: {
        MinRetentionDays: 35,
        MaxRetentionDays: 2555,
        ChangeableForDays: 3,
      },
    });
    template.resourceCountIs("AWS::Backup::BackupPlan", 1);
    template.hasResourceProperties("AWS::Backup::BackupSelection", {
      BackupSelection: {
        Resources: Match.arrayWith([
          Match.objectLike({ "Fn::ImportValue": Match.stringLikeRegexp(".*ControlTable.*") }),
          Match.objectLike({ "Fn::ImportValue": Match.stringLikeRegexp(".*LedgerTable.*") }),
          Match.objectLike({ "Fn::ImportValue": Match.stringLikeRegexp(".*ProposalTable.*") }),
          Match.objectLike({ "Fn::ImportValue": Match.stringLikeRegexp(".*PointerTable.*") }),
          Match.objectLike({ "Fn::ImportValue": Match.stringLikeRegexp(".*EvidenceBucket.*") }),
          Match.objectLike({ "Fn::ImportValue": Match.stringLikeRegexp(".*PackageBucket.*") }),
        ]),
      },
    });
  });
});
