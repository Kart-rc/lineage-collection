import { resolve } from "node:path";

import {
  ArnFormat,
  CfnOutput,
  Stack,
  type StackProps,
  aws_iam as iam,
  aws_logs as logs,
  aws_stepfunctions as sfn,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";
import type { DataStack } from "./data-stack.js";
import type { EnginesStack } from "./engines-stack.js";
import type { NetworkStack } from "./network-stack.js";
import type { PublicationStack } from "./publication-stack.js";
import type { RuntimeStack } from "./runtime-stack.js";
import { RuntimeTarget, lambdaTarget } from "./runtime-assets.js";

export interface OrchestrationStackProps extends StackProps {
  readonly config: PlatformConfig;
  readonly network: NetworkStack;
  readonly data: DataStack;
  readonly engines: EnginesStack;
  readonly runtime: RuntimeStack;
  readonly publication: PublicationStack;
}

const WORKFLOW_FILES = {
  baseline: "baseline.asl.json",
  incremental: "incremental.asl.json",
  "pr-gate": "pr-gate.asl.json",
  nightly: "nightly.asl.json",
} as const;

export class OrchestrationStack extends Stack {
  readonly control: RuntimeTarget;
  readonly coverage: RuntimeTarget;
  readonly workflows: Record<string, sfn.StateMachine>;
  readonly workflowAliases: Record<string, sfn.CfnStateMachineAlias>;

  constructor(scope: Construct, id: string, props: OrchestrationStackProps) {
    super(scope, id, props);
    this.control = new RuntimeTarget(this, "ControlStageTarget", {
      config: props.config,
      network: props.network,
      imageRepository: props.data.lambdaImageRepository,
      target: lambdaTarget("control-stage"),
      environment: props.data.runtimeEnvironment(),
      policyStatements: props.data.dataPlaneStatements("control-stage"),
    });
    this.coverage = new RuntimeTarget(this, "CoverageTarget", {
      config: props.config,
      network: props.network,
      imageRepository: props.data.lambdaImageRepository,
      target: lambdaTarget("coverage"),
      environment: props.data.runtimeEnvironment(),
      policyStatements: props.data.dataPlaneStatements("coverage"),
    });
    this.workflows = {};
    this.workflowAliases = {};
    new CfnOutput(this, "BaselineMapConcurrency", {
      value: String(props.config.baselineMapConcurrency),
    });

    const lambdaVersions = [
      this.control.version,
      this.coverage.version,
      props.runtime.validation.version,
      props.engines.consolidation.version,
      props.engines.proposal.version,
      props.publication.publication.version,
    ];
    const substitutions = {
      // Substitution names are stable generated-contract keys. Their values
      // deliberately point to immutable Lambda versions, so an in-flight
      // workflow cannot cross a handler rollout boundary.
      ControlAliasArn: this.control.version.functionArn,
      CoverageAliasArn: this.coverage.version.functionArn,
      RuntimeValidationAliasArn: props.runtime.validation.version.functionArn,
      ConsolidationAliasArn: props.engines.consolidation.version.functionArn,
      ProposalAliasArn: props.engines.proposal.version.functionArn,
      PublicationAliasArn: props.publication.publication.version.functionArn,
      ScaClusterArn: props.engines.cluster.clusterArn,
      ScaTaskDefinitionArn: props.engines.scaTask.taskDefinitionArn,
      RuntimeSecurityGroupId: props.network.runtimeSecurityGroup.securityGroupId,
      EvidenceBucketName: props.data.evidenceBucket.bucketName,
      Subnet0: props.network.applicationSubnets[0].subnetId,
      Subnet1: props.network.applicationSubnets[1].subnetId,
      Subnet2: props.network.applicationSubnets[2].subnetId,
    };

    for (const [name, file] of Object.entries(WORKFLOW_FILES)) {
      const stateMachineName = `${props.config.resourcePrefix}-${name}`;
      const logGroup = new logs.LogGroup(this, `${name}WorkflowLogs`, {
        retention: props.config.logRetention,
      });
      const role = new iam.Role(this, `${name}WorkflowRole`, {
        assumedBy: new iam.ServicePrincipal("states.amazonaws.com"),
        description: `Scoped execution role for the ${name} lineage workflow`,
      });
      for (const version of lambdaVersions) version.grantInvoke(role);
      role.addToPolicy(
        new iam.PolicyStatement({
          actions: ["ecs:RunTask"],
          resources: [props.engines.scaTask.taskDefinitionArn],
          conditions: {
            ArnEquals: { "ecs:cluster": props.engines.cluster.clusterArn },
          },
        }),
      );
      role.addToPolicy(
        new iam.PolicyStatement({
          actions: ["ecs:DescribeTasks", "ecs:StopTask"],
          resources: [
            this.formatArn({
              service: "ecs",
              resource: "task",
              resourceName: `${props.engines.cluster.clusterName}/*`,
              arnFormat: ArnFormat.SLASH_RESOURCE_NAME,
            }),
          ],
        }),
      );
      props.engines.scaTask.taskRole.grantPassRole(role);
      props.engines.scaTask.executionRole?.grantPassRole(role);
      role.addToPolicy(
        new iam.PolicyStatement({
          actions: ["events:DescribeRule", "events:PutRule", "events:PutTargets"],
          resources: [
            this.formatArn({
              service: "events",
              resource: "rule",
              resourceName: "StepFunctionsGetEventsForECSTaskRule",
            }),
          ],
        }),
      );
      role.addToPolicy(
        new iam.PolicyStatement({
          actions: ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"],
          resources: [`${props.data.evidenceBucket.bucketArn}/*`],
        }),
      );
      role.addToPolicy(
        new iam.PolicyStatement({
          actions: ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"],
          resources: [props.data.key.keyArn],
        }),
      );
      role.addToPolicy(
        new iam.PolicyStatement({
          actions: ["states:StartExecution"],
          resources: [
            this.formatArn({
              service: "states",
              resource: "stateMachine",
              resourceName: stateMachineName,
              arnFormat: ArnFormat.COLON_RESOURCE_NAME,
            }),
          ],
        }),
      );
      role.addToPolicy(
        new iam.PolicyStatement({
          actions: ["states:DescribeExecution", "states:StopExecution"],
          resources: [
            this.formatArn({
              service: "states",
              resource: "execution",
              resourceName: `${stateMachineName}/*`,
              arnFormat: ArnFormat.COLON_RESOURCE_NAME,
            }),
          ],
        }),
      );
      const machine = new sfn.StateMachine(this, `${name}Workflow`, {
        stateMachineName,
        definitionBody: sfn.DefinitionBody.fromFile(
          resolve(import.meta.dirname, `../workflows/${file}`),
        ),
        definitionSubstitutions: substitutions,
        role,
        stateMachineType: sfn.StateMachineType.STANDARD,
        tracingEnabled: true,
        logs: {
          destination: logGroup,
          level: sfn.LogLevel.ALL,
          includeExecutionData: false,
        },
      });
      this.workflows[name] = machine;
      const version = new sfn.CfnStateMachineVersion(this, `${name}WorkflowVersion`, {
        stateMachineArn: machine.stateMachineArn,
        description: `${name} ${props.config.sourceRevision}`,
      });
      const alias = new sfn.CfnStateMachineAlias(this, `${name}WorkflowLiveAlias`, {
        name: "live",
        stateMachineArn: machine.stateMachineArn,
        routingConfiguration: [{ stateMachineVersionArn: version.attrArn, weight: 100 }],
      });
      this.workflowAliases[name] = alias;
      new CfnOutput(this, `${name}WorkflowArn`, { value: machine.stateMachineArn });
      new CfnOutput(this, `${name}WorkflowVersionArn`, { value: version.attrArn });
      new CfnOutput(this, `${name}WorkflowAliasArn`, { value: alias.attrArn });
    }
  }
}
