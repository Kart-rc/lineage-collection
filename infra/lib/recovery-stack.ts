import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  type StackProps,
  aws_kms as kms,
  aws_s3 as s3,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";

export interface RecoveryStackProps extends StackProps {
  readonly config: PlatformConfig;
}

export class RecoveryStack extends Stack {
  readonly key: kms.Key;
  readonly evidenceBucket: s3.Bucket;
  readonly packageBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: RecoveryStackProps) {
    super(scope, id, props);
    const disposable = props.config.environment === "ephemeral";
    this.key = new kms.Key(this, "RecoveryKey", {
      alias: `alias/${props.config.resourcePrefix}-recovery`,
      enableKeyRotation: true,
      multiRegion: true,
      removalPolicy: disposable ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN,
    });
    const truthBucket = (name: string) =>
      new s3.Bucket(this, name, {
        encryption: s3.BucketEncryption.KMS,
        encryptionKey: this.key,
        versioned: true,
        ...(disposable
          ? { autoDeleteObjects: true }
          : {
              objectLockEnabled: true,
              objectLockDefaultRetention: s3.ObjectLockRetention.governance(
                Duration.days(props.config.truthRetentionDays),
              ),
            }),
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        enforceSSL: true,
        removalPolicy: disposable ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN,
      });
    this.evidenceBucket = truthBucket("RecoveryEvidenceBucket");
    this.packageBucket = truthBucket("RecoveryPackageBucket");
    new CfnOutput(this, "RecoveryEvidenceBucketName", { value: this.evidenceBucket.bucketName });
    new CfnOutput(this, "RecoveryPackageBucketName", { value: this.packageBucket.bucketName });
    new CfnOutput(this, "RecoveryKeyArn", { value: this.key.keyArn });
    new CfnOutput(this, "RecoveryRegion", { value: this.region });
  }
}
