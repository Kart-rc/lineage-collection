import {
  Duration,
  Stack,
  type StackProps,
  aws_lambda as lambda,
  aws_lambda_event_sources as lambdaEventSources,
  aws_sqs as sqs,
} from "aws-cdk-lib";
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
    const approvalDlq = new sqs.Queue(this, "ApprovalPublicationDlq", {
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      retentionPeriod: Duration.days(14),
    });
    this.publication.alias.addEventSource(
      new lambdaEventSources.DynamoEventSource(props.data.ledgerTable, {
        startingPosition: lambda.StartingPosition.TRIM_HORIZON,
        batchSize: 10,
        bisectBatchOnError: true,
        reportBatchItemFailures: true,
        retryAttempts: 10,
        maxRecordAge: Duration.hours(23),
        onFailure: new lambdaEventSources.SqsDlq(approvalDlq),
        filters: [
          {
            eventName: ["INSERT"],
            dynamodb: {
              NewImage: { topic: { S: ["PROPOSAL_APPROVED"] } },
            },
          },
        ],
      }),
    );
  }
}
