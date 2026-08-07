import {
  CfnOutput,
  Fn,
  Stack,
  type StackProps,
  aws_ec2 as ec2,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";

export interface NetworkStackProps extends StackProps {
  readonly config: PlatformConfig;
}

export class NetworkStack extends Stack {
  readonly vpc: ec2.Vpc;
  readonly applicationSubnets: ec2.ISubnet[];
  readonly runtimeSecurityGroup: ec2.SecurityGroup;
  readonly graphSecurityGroup: ec2.SecurityGroup;
  readonly endpointSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: NetworkStackProps) {
    super(scope, id, props);
    this.vpc = new ec2.Vpc(this, "Vpc", {
      vpcName: `${props.config.resourcePrefix}-vpc`,
      ...(props.config.primaryAvailabilityZones
        ? { availabilityZones: [...props.config.primaryAvailabilityZones] }
        : { maxAzs: 2 }),
      natGateways: 0,
      subnetConfiguration: [
        {
          name: "application",
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
    });
    this.applicationSubnets = this.vpc.selectSubnets({
      subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
    }).subnets;
    this.runtimeSecurityGroup = new ec2.SecurityGroup(this, "RuntimeSecurityGroup", {
      vpc: this.vpc,
      allowAllOutbound: false,
      description: "Lineage compute to private AWS endpoints and graph only",
    });
    this.graphSecurityGroup = new ec2.SecurityGroup(this, "GraphSecurityGroup", {
      vpc: this.vpc,
      allowAllOutbound: false,
      description: "Neptune ingress from lineage compute only",
    });
    this.graphSecurityGroup.addIngressRule(
      this.runtimeSecurityGroup,
      ec2.Port.tcp(8182),
      "Scoped Neptune access",
    );
    this.runtimeSecurityGroup.addEgressRule(
      this.graphSecurityGroup,
      ec2.Port.tcp(8182),
      "Scoped Neptune access",
    );
    this.runtimeSecurityGroup.addEgressRule(
      ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
      ec2.Port.udp(53),
      "VPC DNS resolution",
    );
    this.runtimeSecurityGroup.addEgressRule(
      ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
      ec2.Port.tcp(53),
      "VPC DNS fallback",
    );
    this.endpointSecurityGroup = new ec2.SecurityGroup(this, "EndpointSecurityGroup", {
      vpc: this.vpc,
      allowAllOutbound: false,
      description: "Private AWS service endpoints accept lineage compute only",
    });
    this.endpointSecurityGroup.addIngressRule(
      this.runtimeSecurityGroup,
      ec2.Port.tcp(443),
      "HTTPS from lineage compute",
    );
    this.runtimeSecurityGroup.addEgressRule(
      this.endpointSecurityGroup,
      ec2.Port.tcp(443),
      "HTTPS to private interface endpoints",
    );
    this.runtimeSecurityGroup.addEgressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      "HTTPS to S3/DynamoDB gateway endpoints; isolated subnets have no NAT or internet route",
    );

    this.vpc.addGatewayEndpoint("S3Endpoint", { service: ec2.GatewayVpcEndpointAwsService.S3 });
    this.vpc.addGatewayEndpoint("DynamoEndpoint", {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
    });
    for (const [name, service] of [
      ["Kinesis", ec2.InterfaceVpcEndpointAwsService.KINESIS_STREAMS],
      ["Secrets", ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER],
      ["Logs", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS],
      ["EcrApi", ec2.InterfaceVpcEndpointAwsService.ECR],
      ["EcrDocker", ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER],
      ["Sqs", ec2.InterfaceVpcEndpointAwsService.SQS],
      ["Events", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_EVENTS],
      ["StepFunctions", ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS],
      ["Sts", ec2.InterfaceVpcEndpointAwsService.STS],
    ] as const) {
      this.vpc.addInterfaceEndpoint(`${name}Endpoint`, {
        service,
        subnets: { subnets: this.applicationSubnets },
        securityGroups: [this.endpointSecurityGroup],
      });
    }
    if (!props.config.enterpriseEndpointServiceName) {
      throw new Error("Enterprise endpoint PrivateLink service name is required");
    }
    const enterpriseEndpoint = this.vpc.addInterfaceEndpoint("EnterpriseEndpoint", {
      service: new ec2.InterfaceVpcEndpointService(
        props.config.enterpriseEndpointServiceName,
        443,
      ),
      privateDnsEnabled: true,
      subnets: { subnets: this.applicationSubnets },
      securityGroups: [this.endpointSecurityGroup],
    });
    new CfnOutput(this, "EnterpriseEndpointId", {
      value: enterpriseEndpoint.vpcEndpointId,
    });
    new CfnOutput(this, "EnterpriseEndpointDnsEntries", {
      value: Fn.join(",", enterpriseEndpoint.vpcEndpointDnsEntries),
    });
  }
}
