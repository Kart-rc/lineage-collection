import {
  CfnOutput,
  Stack,
  type StackProps,
  aws_apigateway as apigateway,
} from "aws-cdk-lib";
import { Construct } from "constructs";

import type { PlatformConfig } from "./config.js";

export interface ApiStackProps extends StackProps {
  readonly config: PlatformConfig;
}

export class ApiStack extends Stack {
  readonly api: apigateway.RestApi;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    this.api = new apigateway.RestApi(this, "ProductApi", {
      restApiName: `${props.config.resourcePrefix}-product-api`,
      deployOptions: {
        stageName: props.config.environment,
        tracingEnabled: true,
        metricsEnabled: true,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        dataTraceEnabled: false,
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
    new CfnOutput(this, "ProductApiUrl", { value: this.api.url });
  }
}
