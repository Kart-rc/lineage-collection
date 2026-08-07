import { Stack, type StackProps } from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";
import type { DataStack } from "./data-stack.js";
import type { NetworkStack } from "./network-stack.js";
import { RuntimeTarget, lambdaTarget } from "./runtime-assets.js";

export interface PublicationStackProps extends StackProps {
  readonly config: PlatformConfig;
  readonly network: NetworkStack;
  readonly data: DataStack;
}

export class PublicationStack extends Stack {
  readonly publication: RuntimeTarget;

  constructor(scope: Construct, id: string, props: PublicationStackProps) {
    super(scope, id, props);
    this.publication = new RuntimeTarget(this, "PublicationTarget", {
      config: props.config,
      network: props.network,
      imageRepository: props.data.lambdaImageRepository,
      target: lambdaTarget("publication"),
      environment: props.data.runtimeEnvironment(),
      policyStatements: props.data.dataPlaneStatements("publication"),
    });
  }
}
