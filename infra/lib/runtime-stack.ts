import { Stack, type StackProps } from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";
import type { DataStack } from "./data-stack.js";
import type { NetworkStack } from "./network-stack.js";
import { RuntimeTarget, lambdaTarget } from "./runtime-assets.js";

export interface RuntimeStackProps extends StackProps {
  readonly config: PlatformConfig;
  readonly network: NetworkStack;
  readonly data: DataStack;
}

export class RuntimeStack extends Stack {
  readonly validation: RuntimeTarget;

  constructor(scope: Construct, id: string, props: RuntimeStackProps) {
    super(scope, id, props);
    this.validation = new RuntimeTarget(this, "RuntimeValidationTarget", {
      config: props.config,
      network: props.network,
      imageRepository: props.data.lambdaImageRepository,
      target: lambdaTarget("runtime-validation"),
      environment: props.data.runtimeEnvironment(),
      policyStatements: props.data.dataPlaneStatements("runtime-validation"),
    });
  }
}
