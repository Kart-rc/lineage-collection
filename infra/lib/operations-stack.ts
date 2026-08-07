import {
  Duration,
  Stack,
  type StackProps,
  aws_backup as backup,
  aws_budgets as budgets,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";
import type { DataStack } from "./data-stack.js";
import type { NetworkStack } from "./network-stack.js";
import { RuntimeTarget, lambdaTarget } from "./runtime-assets.js";

export interface OperationsStackProps extends StackProps {
  readonly config: PlatformConfig;
  readonly network: NetworkStack;
  readonly data: DataStack;
}

export class OperationsStack extends Stack {
  readonly deployment: RuntimeTarget;

  constructor(scope: Construct, id: string, props: OperationsStackProps) {
    super(scope, id, props);
    this.deployment = new RuntimeTarget(this, "DeploymentTarget", {
      config: props.config,
      network: props.network,
      imageRepository: props.data.lambdaImageRepository,
      target: lambdaTarget("deployment"),
      environment: props.data.runtimeEnvironment(),
      policyStatements: props.data.dataPlaneStatements("deployment"),
    });

    const backupVault = new backup.BackupVault(this, "PlatformBackupVault", {
      encryptionKey: props.data.key,
      blockRecoveryPointDeletion: true,
      lockConfiguration: {
        minRetention: Duration.days(35),
        maxRetention: Duration.days(2555),
        changeableFor: Duration.days(3),
      },
    });
    const backupPlan = backup.BackupPlan.daily35DayRetention(
      this,
      "PlatformBackupPlan",
      backupVault,
    );
    backupPlan.addSelection("ProtectedPlatformState", {
      allowRestores: true,
      resources: [
        backup.BackupResource.fromDynamoDbTable(props.data.controlTable),
        backup.BackupResource.fromDynamoDbTable(props.data.ledgerTable),
        backup.BackupResource.fromDynamoDbTable(props.data.proposalTable),
        backup.BackupResource.fromDynamoDbTable(props.data.pointerTable),
        backup.BackupResource.fromArn(props.data.evidenceBucket.bucketArn),
        backup.BackupResource.fromArn(props.data.packageBucket.bucketArn),
      ],
    });

    new budgets.CfnBudget(this, "PlatformBudget", {
      budget: {
        budgetName: `${props.config.resourcePrefix}-monthly`,
        budgetType: "COST",
        timeUnit: "MONTHLY",
        budgetLimit: { amount: props.config.monthlyBudgetUsd, unit: "USD" },
      },
    });
  }
}
