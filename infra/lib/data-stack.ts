import {
  Duration,
  ArnFormat,
  RemovalPolicy,
  Stack,
  type StackProps,
  aws_dynamodb as dynamodb,
  aws_ecr as ecr,
  aws_iam as iam,
  aws_kinesis as kinesis,
  aws_kms as kms,
  aws_neptune as neptune,
  aws_s3 as s3,
  CfnOutput,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";
import type { NetworkStack } from "./network-stack.js";
import type { RecoveryStack } from "./recovery-stack.js";

export interface DataStackProps extends StackProps {
  readonly config: PlatformConfig;
  readonly network: NetworkStack;
  readonly recovery: RecoveryStack;
}

export class DataStack extends Stack {
  readonly key: kms.Key;
  readonly evidenceBucket: s3.Bucket;
  readonly packageBucket: s3.Bucket;
  readonly controlTable: dynamodb.Table;
  readonly ledgerTable: dynamodb.Table;
  readonly proposalTable: dynamodb.Table;
  readonly pointerTable: dynamodb.Table;
  readonly runtimeStream: kinesis.Stream;
  readonly lambdaImageRepository: ecr.Repository;
  readonly scaImageRepository: ecr.Repository;
  readonly graphEndpoint: string;
  readonly graphResourceArn: string;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);
    const disposable = props.config.environment === "ephemeral";
    this.key = new kms.Key(this, "PlatformKey", {
      alias: `alias/${props.config.resourcePrefix}`,
      enableKeyRotation: true,
      removalPolicy: disposable ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN,
    });
    const runtimeRepository = (name: string, repositoryName: string) =>
      new ecr.Repository(this, name, {
        repositoryName: `${props.config.resourcePrefix}/${repositoryName}`,
        encryption: ecr.RepositoryEncryption.KMS,
        encryptionKey: this.key,
        imageScanOnPush: true,
        imageTagMutability: ecr.TagMutability.IMMUTABLE,
        lifecycleRules: [{ maxImageCount: 50, description: "Retain the latest 50 immutable builds" }],
        removalPolicy: disposable ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN,
        emptyOnDelete: disposable,
      });
    this.lambdaImageRepository = runtimeRepository("LambdaImageRepository", "lambda-runtime");
    this.scaImageRepository = runtimeRepository("ScaImageRepository", "sca-runtime");
    const replicationRole = new iam.Role(this, "TruthReplicationRole", {
      assumedBy: new iam.ServicePrincipal("s3.amazonaws.com"),
      description: "Replicates immutable lineage truth to the configured recovery region",
    });
    const truthBucket = (name: string, destination: s3.Bucket) =>
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
        replicationRole,
        replicationRules: [
          {
            id: `${name}-recovery`,
            destination,
            kmsKey: props.recovery.key,
            sseKmsEncryptedObjects: true,
            deleteMarkerReplication: true,
            replicationTimeControl: s3.ReplicationTimeValue.FIFTEEN_MINUTES,
            metrics: s3.ReplicationTimeValue.FIFTEEN_MINUTES,
          },
        ],
      });
    this.evidenceBucket = truthBucket("EvidenceBucket", props.recovery.evidenceBucket);
    this.packageBucket = truthBucket("PackageBucket", props.recovery.packageBucket);
    replicationRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetReplicationConfiguration", "s3:ListBucket"],
        resources: [this.evidenceBucket.bucketArn, this.packageBucket.bucketArn],
      }),
    );
    replicationRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "s3:GetObjectLegalHold",
          "s3:GetObjectRetention",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionForReplication",
          "s3:GetObjectVersionTagging",
        ],
        resources: [`${this.evidenceBucket.bucketArn}/*`, `${this.packageBucket.bucketArn}/*`],
      }),
    );
    replicationRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "s3:ObjectOwnerOverrideToBucketOwner",
          "s3:ReplicateDelete",
          "s3:ReplicateObject",
          "s3:ReplicateTags",
        ],
        resources: [
          `${props.recovery.evidenceBucket.bucketArn}/*`,
          `${props.recovery.packageBucket.bucketArn}/*`,
        ],
      }),
    );
    replicationRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["kms:Decrypt", "kms:DescribeKey"],
        resources: [this.key.keyArn],
      }),
    );
    replicationRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"],
        resources: [props.recovery.key.keyArn],
      }),
    );

    const controlTable = (name: string, stream?: dynamodb.StreamViewType) =>
      new dynamodb.Table(this, name, {
        partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
        encryptionKey: this.key,
        pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
        deletionProtection: !disposable,
        removalPolicy: disposable ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN,
        stream,
      });
    this.controlTable = controlTable("ControlTable");
    this.ledgerTable = controlTable("LedgerTable", dynamodb.StreamViewType.NEW_IMAGE);
    this.proposalTable = controlTable("ProposalTable");
    this.pointerTable = controlTable("PointerTable");
    this.ledgerTable.addGlobalSecondaryIndex({
      indexName: "RunsByUpdatedAt",
      partitionKey: { name: "queryPk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "querySk", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    this.proposalTable.addGlobalSecondaryIndex({
      indexName: "ProposalsByState",
      partitionKey: { name: "queryPk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "querySk", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    this.runtimeStream = new kinesis.Stream(this, "RuntimeStream", {
      encryption: kinesis.StreamEncryption.KMS,
      encryptionKey: this.key,
      streamMode: kinesis.StreamMode.ON_DEMAND,
      retentionPeriod: undefined,
    });

    const subnetGroup = new neptune.CfnDBSubnetGroup(this, "GraphSubnetGroup", {
      dbSubnetGroupDescription: "Private lineage graph subnets",
      subnetIds: props.network.applicationSubnets.map((subnet) => subnet.subnetId),
    });
    const cluster = new neptune.CfnDBCluster(this, "GraphCluster", {
      availabilityZones: props.network.vpc.availabilityZones.slice(0, 3),
      backupRetentionPeriod: 7,
      dbSubnetGroupName: subnetGroup.ref,
      deletionProtection: !disposable,
      iamAuthEnabled: true,
      kmsKeyId: this.key.keyArn,
      storageEncrypted: true,
      vpcSecurityGroupIds: [props.network.graphSecurityGroup.securityGroupId],
    });
    for (let index = 0; index < 3; index += 1) {
      const instance = new neptune.CfnDBInstance(this, `GraphInstance${index + 1}`, {
        dbClusterIdentifier: cluster.ref,
        dbInstanceClass: "db.r6g.large",
        availabilityZone: props.network.vpc.availabilityZones[index],
      });
      instance.addResourceDependency(cluster);
    }
    this.graphEndpoint = cluster.attrEndpoint;
    this.graphResourceArn = this.formatArn({
      service: "neptune-db",
      resource: "cluster",
      resourceName: `${cluster.attrClusterResourceId}/*`,
      arnFormat: ArnFormat.SLASH_RESOURCE_NAME,
    });
    new CfnOutput(this, "ControlTableName", { value: this.controlTable.tableName });
    new CfnOutput(this, "LedgerTableName", { value: this.ledgerTable.tableName });
    new CfnOutput(this, "EvidenceBucketName", { value: this.evidenceBucket.bucketName });
    new CfnOutput(this, "PackageBucketName", { value: this.packageBucket.bucketName });
    new CfnOutput(this, "LambdaImageRepositoryUri", {
      value: this.lambdaImageRepository.repositoryUri,
    });
    new CfnOutput(this, "ScaImageRepositoryUri", {
      value: this.scaImageRepository.repositoryUri,
    });
  }

  runtimeEnvironment(): Record<string, string> {
    return {
      LINEAGE_CONTROL_TABLE: this.controlTable.tableName,
      LINEAGE_LEDGER_TABLE: this.ledgerTable.tableName,
      LINEAGE_PROPOSAL_TABLE: this.proposalTable.tableName,
      LINEAGE_POINTER_TABLE: this.pointerTable.tableName,
      LINEAGE_EVIDENCE_BUCKET: this.evidenceBucket.bucketName,
      LINEAGE_PACKAGE_BUCKET: this.packageBucket.bucketName,
      LINEAGE_RUNTIME_STREAM: this.runtimeStream.streamName,
      LINEAGE_NEPTUNE_ENDPOINT: this.graphEndpoint,
    };
  }

  dataPlaneStatements(target: string): iam.PolicyStatement[] {
    const tablesByTarget: Record<string, dynamodb.Table[]> = {
      intake: [this.ledgerTable],
      "control-stage": [this.controlTable, this.ledgerTable, this.pointerTable],
      classification: [this.controlTable, this.ledgerTable],
      "runtime-validation": [this.controlTable, this.ledgerTable],
      consolidation: [this.controlTable, this.ledgerTable],
      coverage: [this.controlTable, this.ledgerTable],
      proposal: [this.controlTable, this.ledgerTable, this.proposalTable],
      publication: [this.ledgerTable, this.proposalTable, this.pointerTable],
      deployment: [this.controlTable, this.ledgerTable, this.pointerTable],
      "product-api": [
        this.controlTable,
        this.ledgerTable,
        this.proposalTable,
        this.pointerTable,
      ],
      sca: [this.controlTable, this.ledgerTable],
    };
    const bucketsByTarget: Record<string, s3.Bucket[]> = {
      intake: [this.evidenceBucket],
      "control-stage": [this.evidenceBucket, this.packageBucket],
      classification: [this.evidenceBucket, this.packageBucket],
      "runtime-validation": [this.evidenceBucket],
      consolidation: [this.evidenceBucket],
      coverage: [this.evidenceBucket, this.packageBucket],
      proposal: [this.evidenceBucket, this.packageBucket],
      publication: [this.evidenceBucket, this.packageBucket],
      deployment: [this.packageBucket],
      "product-api": [this.evidenceBucket],
      sca: [this.evidenceBucket, this.packageBucket],
    };
    const tables = tablesByTarget[target];
    const buckets = bucketsByTarget[target];
    if (!tables || !buckets) throw new Error(`Unknown data-plane target: ${target}`);
    if (target === "product-api") {
      const readResources = tables.flatMap((table) => [
        table.tableArn,
        `${table.tableArn}/index/*`,
      ]);
      return [
        new iam.PolicyStatement({
          actions: ["dynamodb:GetItem", "dynamodb:Query"],
          resources: readResources,
        }),
        new iam.PolicyStatement({
          actions: ["dynamodb:TransactWriteItems"],
          resources: [
            this.controlTable.tableArn,
            this.ledgerTable.tableArn,
            this.proposalTable.tableArn,
          ],
        }),
        new iam.PolicyStatement({
          actions: ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"],
          resources: [`${this.evidenceBucket.bucketArn}/*`],
        }),
        new iam.PolicyStatement({
          actions: ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"],
          resources: [this.key.keyArn],
        }),
        new iam.PolicyStatement({
          actions: ["neptune-db:Connect"],
          resources: [this.graphResourceArn],
        }),
      ];
    }
    const statements = [
      new iam.PolicyStatement({
        actions: [
          "dynamodb:BatchGetItem",
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Query",
          "dynamodb:TransactWriteItems",
          "dynamodb:UpdateItem",
        ],
        resources: tables.flatMap((table) => [table.tableArn, `${table.tableArn}/index/*`]),
      }),
      new iam.PolicyStatement({
        actions: ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"],
        resources: buckets.map((bucket) => `${bucket.bucketArn}/*`),
      }),
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: buckets.map((bucket) => bucket.bucketArn),
      }),
      new iam.PolicyStatement({
        actions: ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"],
        resources: [this.key.keyArn],
      }),
    ];
    if (target === "runtime-validation") {
      statements.push(
        new iam.PolicyStatement({
          actions: ["kinesis:GetRecords", "kinesis:GetShardIterator", "kinesis:PutRecord"],
          resources: [this.runtimeStream.streamArn],
        }),
      );
    }
    if (
      target === "coverage" ||
      target === "publication" ||
      target === "deployment" ||
      target === "product-api"
    ) {
      statements.push(
        new iam.PolicyStatement({
          actions: ["neptune-db:Connect"],
          resources: [this.graphResourceArn],
        }),
      );
    }
    return statements;
  }
}
