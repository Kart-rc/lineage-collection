#!/usr/bin/env node
import { App, Tags } from "aws-cdk-lib";

import { ApiStack } from "../lib/api-stack.js";
import { applyExplicitAwsContext, loadPlatformConfig } from "../lib/config.js";
import { DataStack } from "../lib/data-stack.js";
import { EnginesStack } from "../lib/engines-stack.js";
import { IntakeStack } from "../lib/intake-stack.js";
import { NetworkStack } from "../lib/network-stack.js";
import { OperationsStack } from "../lib/operations-stack.js";
import { OrchestrationStack } from "../lib/orchestration-stack.js";
import { PublicationStack } from "../lib/publication-stack.js";
import { RecoveryStack } from "../lib/recovery-stack.js";
import { RuntimeStack } from "../lib/runtime-stack.js";

const app = new App();
const config = loadPlatformConfig(app.node);
applyExplicitAwsContext(app.node, config);
const env = config.account && config.primaryRegion
  ? { account: config.account, region: config.primaryRegion }
  : undefined;
const recoveryEnv = config.account && config.secondaryRegion
  ? { account: config.account, region: config.secondaryRegion }
  : undefined;
const recovery = new RecoveryStack(app, "LineageRecovery", {
  config,
  env: recoveryEnv,
  crossRegionReferences: true,
});
const network = new NetworkStack(app, "LineageNetwork", { config, env });
const data = new DataStack(app, "LineageData", {
  config,
  network,
  recovery,
  env,
  crossRegionReferences: true,
});
new IntakeStack(app, "LineageIntake", { config, network, data, env });
new OrchestrationStack(app, "LineageOrchestration", { config, network, data, env });
new EnginesStack(app, "LineageEngines", { config, network, data, env });
new RuntimeStack(app, "LineageRuntime", { config, network, data, env });
new PublicationStack(app, "LineagePublication", { config, network, data, env });
new ApiStack(app, "LineageApi", { config, env });
new OperationsStack(app, "LineageOperations", { config, network, data, env });

Tags.of(app).add("Application", "lineage-collector");
Tags.of(app).add("Environment", config.environment);
Tags.of(app).add("ManagedBy", "aws-cdk");
