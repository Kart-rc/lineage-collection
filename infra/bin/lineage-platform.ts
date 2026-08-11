#!/usr/bin/env node
import { App, Tags } from "aws-cdk-lib";
import { fileURLToPath } from "node:url";

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
import { WebStack } from "../lib/web-stack.js";

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
  stackName: `${config.resourcePrefix}-recovery`,
  env: recoveryEnv,
  crossRegionReferences: true,
});
const network = new NetworkStack(app, "LineageNetwork", {
  config,
  stackName: `${config.resourcePrefix}-network`,
  env,
});
const data = new DataStack(app, "LineageData", {
  config,
  stackName: `${config.resourcePrefix}-data`,
  network,
  recovery,
  env,
  crossRegionReferences: true,
});
const engines = new EnginesStack(app, "LineageEngines", {
  config,
  network,
  data,
  stackName: `${config.resourcePrefix}-engines`,
  env,
});
const runtime = new RuntimeStack(app, "LineageRuntime", {
  config,
  network,
  data,
  stackName: `${config.resourcePrefix}-runtime`,
  env,
});
const publication = new PublicationStack(app, "LineagePublication", {
  config,
  network,
  data,
  stackName: `${config.resourcePrefix}-publication`,
  env,
});
const orchestration = new OrchestrationStack(app, "LineageOrchestration", {
  config,
  network,
  data,
  engines,
  runtime,
  publication,
  stackName: `${config.resourcePrefix}-orchestration`,
  env,
});
new IntakeStack(app, "LineageIntake", {
  config,
  network,
  data,
  orchestration,
  stackName: `${config.resourcePrefix}-intake`,
  env,
});
const api = new ApiStack(app, "LineageApi", {
  config,
  network,
  data,
  stackName: `${config.resourcePrefix}-api`,
  env,
});
new WebStack(app, "LineageWeb", {
  config,
  api,
  webAssetPath: fileURLToPath(new URL("../../apps/web/dist", import.meta.url)),
  stackName: `${config.resourcePrefix}-web`,
  env,
});
new OperationsStack(app, "LineageOperations", {
  config,
  network,
  data,
  stackName: `${config.resourcePrefix}-operations`,
  env,
});

Tags.of(app).add("Application", "lineage-collector");
Tags.of(app).add("Environment", config.environment);
Tags.of(app).add("ManagedBy", "aws-cdk");
