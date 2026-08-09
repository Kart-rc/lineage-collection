import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { LAMBDA_TARGETS, createBuildMetadata } from "../lib/runtime-assets.js";

describe("runtime assets", () => {
  it("declares every independently deployable handler boundary", () => {
    expect(LAMBDA_TARGETS.map((target) => target.name)).toEqual([
      "intake",
      "control-stage",
      "classification",
      "runtime-validation",
      "consolidation",
      "coverage",
      "proposal",
      "publication",
      "deployment",
    ]);
    expect(new Set(LAMBDA_TARGETS.map((target) => target.handler)).size).toBe(9);
    for (const target of LAMBDA_TARGETS) {
      expect(target.memoryMiB).toBeGreaterThanOrEqual(256);
      expect(target.timeoutSeconds).toBeGreaterThan(0);
      expect(target.reservedConcurrency).toBeGreaterThan(0);
    }
  });

  it("creates deterministic bounded build metadata", () => {
    const first = createBuildMetadata({
      sourceRevision: "abc123",
      dependencyLockDigest: "sha256:lock",
      imageAssetDigest: "sha256:image",
      images: {
        lambda: {
          platform: "linux/amd64",
          digest: `sha256:${"a".repeat(64)}`,
          archiveDigest: `sha256:${"b".repeat(64)}`,
        },
        sca: {
          platform: "linux/arm64",
          digest: `sha256:${"c".repeat(64)}`,
          archiveDigest: `sha256:${"d".repeat(64)}`,
        },
      },
    });
    const second = createBuildMetadata({
      sourceRevision: "abc123",
      dependencyLockDigest: "sha256:lock",
      imageAssetDigest: "sha256:image",
      images: first.images,
    });
    expect(first).toEqual(second);
    expect(first.handlers).toHaveLength(9);
    expect(first).toHaveProperty("imageAssetDigest", "sha256:image");
    expect(first).toHaveProperty("images.lambda.platform", "linux/amd64");
    expect(first).toHaveProperty("images.lambda.digest", `sha256:${"a".repeat(64)}`);
    expect(first).toHaveProperty("images.sca.platform", "linux/arm64");
    expect(first).toHaveProperty("images.sca.digest", `sha256:${"c".repeat(64)}`);
    expect(first).toHaveProperty("cdkContext.lambdaImageDigest", `sha256:${"a".repeat(64)}`);
    expect(first).toHaveProperty("cdkContext.scaImageDigest", `sha256:${"c".repeat(64)}`);
    expect(JSON.stringify(first)).not.toContain("RESOLVED_AT_ASSET_PUBLISH");
    expect(JSON.stringify(first).length).toBeLessThan(8192);
  });

  it("uses a Lambda image and a separately bounded non-root SCA image", () => {
    const lambdaDockerfile = readFileSync(resolve("assets/lambda/Dockerfile"), "utf8");
    const scaDockerfile = readFileSync(resolve("assets/sca/Dockerfile"), "utf8");
    expect(lambdaDockerfile).toMatch(
      /public\.ecr\.aws\/lambda\/python:3\.12@sha256:[0-9a-f]{64}/,
    );
    expect(scaDockerfile).toMatch(/python:3\.12-slim@sha256:[0-9a-f]{64}/);
    expect(lambdaDockerfile).toContain("uv build");
    expect(scaDockerfile).toContain("uv build");
    expect(scaDockerfile).toMatch(/USER\s+lineage/);
    expect(scaDockerfile).toContain("ENV TMPDIR=/opt/lineage-scratch");
    expect(scaDockerfile).toContain("lineage_api.entrypoints.sca_worker");
  });
});
