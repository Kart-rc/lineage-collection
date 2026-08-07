import {
  CfnOutput,
  Duration,
  Stack,
  type StackProps,
  aws_events as events,
  aws_cloudwatch as cloudwatch,
  aws_cloudwatch_actions as cloudwatchActions,
  aws_iam as iam,
  aws_sqs as sqs,
  aws_sns as sns,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";
import type { DataStack } from "./data-stack.js";
import type { NetworkStack } from "./network-stack.js";
import { RuntimeTarget, lambdaTarget } from "./runtime-assets.js";

export interface IntakeStackProps extends StackProps {
  readonly config: PlatformConfig;
  readonly network: NetworkStack;
  readonly data: DataStack;
}

export class IntakeStack extends Stack {
  readonly eventBus: events.EventBus;
  readonly queues: Record<string, sqs.Queue>;
  readonly intake: RuntimeTarget;

  constructor(scope: Construct, id: string, props: IntakeStackProps) {
    super(scope, id, props);
    this.eventBus = new events.EventBus(this, "IntakeBus", {
      eventBusName: `${props.config.resourcePrefix}-intake`,
    });
    new events.Archive(this, "IntakeArchive", {
      sourceEventBus: this.eventBus,
      retention: Duration.days(props.config.archiveRetentionDays),
      eventPattern: { source: ["lineage.intake", "lineage.provider"] },
    });

    this.queues = {};
    const pagingTopic = props.config.pagingTopicArn
      ? sns.Topic.fromTopicArn(this, "PagingTopic", props.config.pagingTopicArn)
      : undefined;
    for (const lane of ["interactive", "events", "batch"] as const) {
      const fifo = lane !== "batch";
      const deadLetterQueue = new sqs.Queue(this, `${lane}Dlq`, {
        queueName: `${props.config.resourcePrefix}-${lane}-dlq${fifo ? ".fifo" : ""}`,
        fifo,
        contentBasedDeduplication: fifo,
        encryption: sqs.QueueEncryption.SQS_MANAGED,
        enforceSSL: true,
        retentionPeriod: Duration.days(14),
      });
      this.queues[lane] = new sqs.Queue(this, `${lane}Queue`, {
        queueName: `${props.config.resourcePrefix}-${lane}${fifo ? ".fifo" : ""}`,
        fifo,
        contentBasedDeduplication: fifo,
        encryption: sqs.QueueEncryption.SQS_MANAGED,
        enforceSSL: true,
        visibilityTimeout: Duration.minutes(6),
        deadLetterQueue: { maxReceiveCount: 5, queue: deadLetterQueue },
      });
      const ageAlarm = new cloudwatch.Alarm(this, `${lane}AgeAlarm`, {
        metric: this.queues[lane].metricApproximateAgeOfOldestMessage(),
        threshold: lane === "interactive" ? 120 : 900,
        evaluationPeriods: 2,
        alarmDescription: `${lane} lineage lane is not draining within its bounded window`,
      });
      const deadLetterAlarm = new cloudwatch.Alarm(this, `${lane}DeadLetterAlarm`, {
        metric: deadLetterQueue.metricApproximateNumberOfMessagesVisible(),
        threshold: 1,
        evaluationPeriods: 1,
        alarmDescription: `${lane} lineage lane has dead-lettered work`,
      });
      if (pagingTopic) {
        const action = new cloudwatchActions.SnsAction(pagingTopic);
        ageAlarm.addAlarmAction(action);
        deadLetterAlarm.addAlarmAction(action);
      }
    }
    const queueStatement = new iam.PolicyStatement({
      actions: [
        "sqs:ChangeMessageVisibility",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:ReceiveMessage",
        "sqs:SendMessage",
      ],
      resources: Object.values(this.queues).map((queue) => queue.queueArn),
    });
    this.intake = new RuntimeTarget(this, "IntakeTarget", {
      config: props.config,
      network: props.network,
      imageRepository: props.data.lambdaImageRepository,
      target: lambdaTarget("intake"),
      environment: {
        ...props.data.runtimeEnvironment(),
        LINEAGE_INTERACTIVE_QUEUE_URL: this.queues.interactive.queueUrl,
        LINEAGE_EVENTS_QUEUE_URL: this.queues.events.queueUrl,
        LINEAGE_BATCH_QUEUE_URL: this.queues.batch.queueUrl,
        LINEAGE_EVENT_BUS_NAME: this.eventBus.eventBusName,
      },
      policyStatements: [...props.data.dataPlaneStatements("intake"), queueStatement],
    });
    for (const [lane, queue] of Object.entries(this.queues)) {
      new CfnOutput(this, `${lane}QueueUrl`, { value: queue.queueUrl });
    }
  }
}
