import {
  CfnOutput,
  Stack,
  type StackProps,
  aws_apigateway as apigateway,
  aws_logs as logs,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";
import type { DataStack } from "./data-stack.js";
import type { NetworkStack } from "./network-stack.js";
import { RuntimeTarget, lambdaTarget } from "./runtime-assets.js";

export interface ApiStackProps extends StackProps {
  readonly config: PlatformConfig;
  readonly network: NetworkStack;
  readonly data: DataStack;
}

export class ApiStack extends Stack {
  readonly api: apigateway.RestApi;
  readonly product: RuntimeTarget;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    this.product = new RuntimeTarget(this, "ProductApiTarget", {
      config: props.config,
      network: props.network,
      imageRepository: props.data.lambdaImageRepository,
      target: lambdaTarget("product-api"),
      environment: props.data.runtimeEnvironment(),
      policyStatements: props.data.dataPlaneStatements("product-api"),
    });
    const accessLogs = new logs.LogGroup(this, "AccessLogs", {
      retention: props.config.logRetention,
    });
    this.api = new apigateway.RestApi(this, "ProductApi", {
      restApiName: `${props.config.resourcePrefix}-product-api`,
      deployOptions: {
        stageName: props.config.environment,
        tracingEnabled: true,
        metricsEnabled: true,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        dataTraceEnabled: false,
        accessLogDestination: new apigateway.LogGroupLogDestination(accessLogs),
        accessLogFormat: apigateway.AccessLogFormat.custom(
          JSON.stringify({
            requestId: "$context.requestId",
            sourceIp: "$context.identity.sourceIp",
            principalId: "$context.identity.userArn",
            httpMethod: "$context.httpMethod",
            resourcePath: "$context.resourcePath",
            status: "$context.status",
            responseLength: "$context.responseLength",
            integrationLatency: "$context.integrationLatency",
          }),
        ),
      },
      endpointConfiguration: { types: [apigateway.EndpointType.REGIONAL] },
    });
    const health = this.api.root.addResource("healthz");
    health.addMethod(
      "GET",
      new apigateway.MockIntegration({
        integrationResponses: [{ statusCode: "200" }],
        requestTemplates: { "application/json": '{"statusCode": 200}' },
      }),
      { methodResponses: [{ statusCode: "200" }], authorizationType: apigateway.AuthorizationType.IAM },
    );
    const proxy = this.api.root.addResource("{proxy+}");
    proxy.addMethod(
      "ANY",
      new apigateway.LambdaIntegration(this.product.alias, {
        proxy: true,
        allowTestInvoke: false,
      }),
      { authorizationType: apigateway.AuthorizationType.IAM },
    );
    new CfnOutput(this, "ProductApiUrl", { value: this.api.url });
  }
}
