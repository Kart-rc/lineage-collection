import {
  CfnOutput,
  Duration,
  Stack,
  type StackProps,
  aws_logs as logs,
  aws_stepfunctions as sfn,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";
import type { DataStack } from "./data-stack.js";
import type { NetworkStack } from "./network-stack.js";
import { RuntimeTarget, lambdaTarget } from "./runtime-assets.js";

export interface OrchestrationStackProps extends StackProps {
  readonly config: PlatformConfig;
  readonly network: NetworkStack;
  readonly data: DataStack;
}

export class OrchestrationStack extends Stack {
  readonly control: RuntimeTarget;
  readonly coverage: RuntimeTarget;
  readonly workflows: Record<string, sfn.StateMachine>;

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
    new CfnOutput(this, "BaselineMapConcurrency", {
      value: String(props.config.baselineMapConcurrency),
    });
    for (const name of ["baseline", "incremental", "pr-gate", "nightly"] as const) {
      const logGroup = new logs.LogGroup(this, `${name}WorkflowLogs`, {
        retention: props.config.logRetention,
      });
      const definition = new sfn.Pass(this, `${name}PinnedDefinition`, {
        comment: "Task 19 replaces this deployable pinned shell with exported executable ASL",
      });
      this.workflows[name] = new sfn.StateMachine(this, `${name}Workflow`, {
        stateMachineName: `${props.config.resourcePrefix}-${name}`,
        definitionBody: sfn.DefinitionBody.fromChainable(definition),
        stateMachineType: sfn.StateMachineType.STANDARD,
        timeout: Duration.hours(24),
        tracingEnabled: true,
        logs: { destination: logGroup, level: sfn.LogLevel.ALL, includeExecutionData: false },
      });
      new CfnOutput(this, `${name}WorkflowArn`, { value: this.workflows[name].stateMachineArn });
    }
  }
}
