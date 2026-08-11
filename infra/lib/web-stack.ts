import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  type StackProps,
  aws_cloudfront as cloudfront,
  aws_cloudfront_origins as origins,
  aws_s3 as s3,
  aws_s3_deployment as deployment,
} from "aws-cdk-lib";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { Construct } from "constructs";

import type { ApiStack } from "./api-stack.js";
import type { PlatformConfig } from "./config.js";

export interface WebStackProps extends StackProps {
  readonly config: PlatformConfig;
  readonly api: ApiStack;
  readonly webAssetPath: string;
}

const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "base-uri 'self'",
  "connect-src 'self'",
  "font-src 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "img-src 'self' data:",
  "object-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
].join("; ");

export function renderRuntimeConfig(config: PlatformConfig): string {
  const publicRuntimeConfig = {
    schemaVersion: "1.0.0",
    environment: config.environment,
    apiBasePath: "/api",
    sourceRevision: config.sourceRevision,
    demoActions: false,
  };
  return `window.__LINEAGE_RUNTIME_CONFIG__=Object.freeze(${JSON.stringify(publicRuntimeConfig)});\n`;
}

function validateWebBuild(assetPath: string): string {
  const resolved = resolve(assetPath);
  const indexPath = join(resolved, "index.html");
  const assetsPath = join(resolved, "assets");
  if (!existsSync(indexPath) || !existsSync(assetsPath)) {
    throw new Error("Web build must contain index.html and an assets directory");
  }
  const index = readFileSync(indexPath, "utf8");
  if (
    !/<script\s+src=["']\/runtime-config\.js["']\s*><\/script>/.test(index) ||
    !/<script\b(?=[^>]*type=["']module["'])(?=[^>]*src=["']\/assets\/)[^>]*>/.test(
      index,
    )
  ) {
    throw new Error(
      "Web build must synchronously load runtime-config.js before its deferred module executes",
    );
  }
  const assets = readdirSync(assetsPath, { withFileTypes: true });
  if (
    !assets.some((entry) => entry.name.endsWith(".js")) ||
    !assets.some((entry) => entry.name.endsWith(".css")) ||
    assets.some((entry) => !entry.isFile())
  ) {
    throw new Error("Web build assets must be a flat immutable file set");
  }
  if (assets.some((entry) => entry.name.endsWith(".map"))) {
    throw new Error("Production web build must not contain source maps");
  }
  if (
    assets.some(
      (entry) => !/-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$/.test(entry.name),
    )
  ) {
    throw new Error("Production web assets must include immutable content hashes");
  }
  return resolved;
}

export class WebStack extends Stack {
  readonly distribution: cloudfront.Distribution;
  readonly siteBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: WebStackProps) {
    super(scope, id, props);
    const webAssetPath = validateWebBuild(props.webAssetPath);
    const disposable = props.config.environment !== "production";
    const removalPolicy = disposable ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN;

    this.siteBucket = new s3.Bucket(this, "SiteBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      autoDeleteObjects: disposable,
      removalPolicy,
    });
    const accessLogBucket = new s3.Bucket(this, "AccessLogBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      accessControl: s3.BucketAccessControl.LOG_DELIVERY_WRITE,
      objectOwnership: s3.ObjectOwnership.OBJECT_WRITER,
      lifecycleRules: [
        { expiration: Duration.days(props.config.archiveRetentionDays) },
      ],
      autoDeleteObjects: disposable,
      removalPolicy,
    });
    const securityHeaders = new cloudfront.ResponseHeadersPolicy(
      this,
      "SecurityHeaders",
      {
        securityHeadersBehavior: {
          contentSecurityPolicy: {
            contentSecurityPolicy: CONTENT_SECURITY_POLICY,
            override: true,
          },
          contentTypeOptions: { override: true },
          frameOptions: {
            frameOption: cloudfront.HeadersFrameOption.DENY,
            override: true,
          },
          referrerPolicy: {
            referrerPolicy: cloudfront.HeadersReferrerPolicy.SAME_ORIGIN,
            override: true,
          },
          strictTransportSecurity: {
            accessControlMaxAge: Duration.days(730),
            includeSubdomains: true,
            preload: true,
            override: true,
          },
        },
        customHeadersBehavior: {
          customHeaders: [
            {
              header: "Permissions-Policy",
              value: "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
              override: true,
            },
          ],
        },
      },
    );
    const spaRewrite = new cloudfront.Function(this, "SpaRewrite", {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(`
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.indexOf('/api/') === 0 || uri.indexOf('/assets/') === 0 ||
      uri === '/runtime-config.js' || uri === '/index.html') return request;
  request.uri = '/index.html';
  return request;
}
`),
    });
    const staticOrigin = origins.S3BucketOrigin.withOriginAccessControl(
      this.siteBucket,
    );
    const apiOrigin = new origins.RestApiOrigin(props.api.api);
    this.distribution = new cloudfront.Distribution(this, "Distribution", {
      defaultRootObject: "index.html",
      enableIpv6: true,
      enableLogging: true,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      logBucket: accessLogBucket,
      logFilePrefix: "cloudfront/",
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      defaultBehavior: {
        origin: staticOrigin,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
        cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        compress: true,
        responseHeadersPolicy: securityHeaders,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        functionAssociations: [
          {
            eventType: cloudfront.FunctionEventType.VIEWER_REQUEST,
            function: spaRewrite,
          },
        ],
      },
      additionalBehaviors: {
        "assets/*": {
          origin: staticOrigin,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
          cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
          compress: true,
          responseHeadersPolicy: securityHeaders,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        },
        "api/*": {
          origin: apiOrigin,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy:
            cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          compress: true,
          responseHeadersPolicy: securityHeaders,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        },
      },
    });

    const webSource = deployment.Source.asset(webAssetPath, {
      exclude: ["runtime-config.js"],
    });
    const hashedAssets = new deployment.BucketDeployment(this, "HashedAssets", {
      sources: [webSource],
      destinationBucket: this.siteBucket,
      exclude: ["*"],
      include: ["assets/*"],
      prune: false,
      retainOnDelete: !disposable,
      cacheControl: [
        deployment.CacheControl.setPublic(),
        deployment.CacheControl.maxAge(Duration.days(365)),
        deployment.CacheControl.immutable(),
      ],
    });
    const shell = new deployment.BucketDeployment(this, "Shell", {
      sources: [webSource],
      destinationBucket: this.siteBucket,
      exclude: ["assets/*", "runtime-config.js"],
      prune: false,
      retainOnDelete: !disposable,
      cacheControl: [
        deployment.CacheControl.noCache(),
        deployment.CacheControl.noStore(),
        deployment.CacheControl.mustRevalidate(),
      ],
      distribution: this.distribution,
      distributionPaths: ["/", "/index.html"],
    });
    shell.node.addDependency(hashedAssets);

    const runtimeConfig = new deployment.BucketDeployment(this, "RuntimeConfig", {
      sources: [
        deployment.Source.data(
          "runtime-config.js",
          renderRuntimeConfig(props.config),
        ),
      ],
      destinationBucket: this.siteBucket,
      prune: false,
      retainOnDelete: !disposable,
      cacheControl: [
        deployment.CacheControl.noCache(),
        deployment.CacheControl.noStore(),
        deployment.CacheControl.mustRevalidate(),
      ],
      distribution: this.distribution,
      distributionPaths: ["/runtime-config.js"],
    });
    runtimeConfig.node.addDependency(shell);

    new CfnOutput(this, "WebUrl", {
      value: `https://${this.distribution.distributionDomainName}`,
    });
  }
}
