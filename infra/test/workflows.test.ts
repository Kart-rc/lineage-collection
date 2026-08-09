import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { App } from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { describe, expect, it } from "vitest";

import { fixtureConfig } from "../lib/config.js";
import { DataStack } from "../lib/data-stack.js";
import { EnginesStack } from "../lib/engines-stack.js";
import { IntakeStack } from "../lib/intake-stack.js";
import { NetworkStack } from "../lib/network-stack.js";
import { OrchestrationStack } from "../lib/orchestration-stack.js";
import { PublicationStack } from "../lib/publication-stack.js";
import { RecoveryStack } from "../lib/recovery-stack.js";
import { RuntimeStack } from "../lib/runtime-stack.js";

const workflowsRoot = resolve("workflows");
const contracts = JSON.parse(
  readFileSync(resolve(workflowsRoot, "generated/workflow-contracts.json"), "utf8"),
);

const cases = [
  ["BASELINE", "baseline.asl.json", "B", 10],
  ["INCREMENTAL", "incremental.asl.json", "I", 10],
  ["PR_GATE", "pr-gate.asl.json", "P", 8],
  ["NIGHTLY", "nightly.asl.json", "N", 6],
] as const;

function allTaskStates(state: any): any[] {
  const tasks: any[] = [];
  if (state.Type === "Task") tasks.push(state);
  if (state.Type === "Map") {
    for (const nested of Object.values(state.ItemProcessor.States)) {
      tasks.push(...allTaskStates(nested));
    }
  }
  return tasks;
}

describe("exported executable workflow topology", () => {
  it.each(cases)("maps exact %s stage contracts into bounded ASL", (kind, file, prefix, count) => {
    const contract = contracts.workflows[kind];
    const definition = JSON.parse(readFileSync(resolve(workflowsRoot, file), "utf8"));
    const expected = Array.from({ length: count }, (_, index) => `${prefix}${index + 1}`);

    expect(contract.stages.map((stage: any) => stage.stageId)).toEqual(expected);
    expect(contract.version).toBe("1.0.0");
    expect(definition.StartAt).toBe(expected[0]);
    expect(definition.TimeoutSeconds).toBe(contract.workflowTimeoutSeconds);
    for (const terminal of contract.terminalStates) {
      expect(["Succeed", "Fail"]).toContain(definition.States[terminal].Type);
    }
    for (const stage of contract.stages) {
      const state = definition.States[stage.stageId];
      expect(["Task", "Map"]).toContain(state.Type);
      expect(state.Catch).toBeDefined();
      const tasks = allTaskStates(state);
      for (const task of tasks) {
        expect(task.TimeoutSeconds).toBeLessThanOrEqual(stage.timeoutSeconds);
        expect(task.Resource).toMatch(/lambda:invoke|ecs:runTask\.waitForTaskToken/);
        expect(task.Catch ?? state.Catch).toBeDefined();
        for (const retry of task.Retry) {
          expect(retry.MaxAttempts).toBeGreaterThan(0);
          expect(retry.MaxAttempts).toBeLessThanOrEqual(5);
          expect(retry.BackoffRate).toBeGreaterThanOrEqual(1);
        }
        if (task.Resource.includes("lambda:invoke")) {
          expect(task.Parameters.Payload["input.$"]).toMatch(/^\$\./);
          expect(task.Parameters.Payload).not.toHaveProperty("body");
        } else {
          expect(task.HeartbeatSeconds).toBeGreaterThan(0);
          expect(JSON.stringify(task.Parameters)).toContain("LINEAGE_TASK_TOKEN");
        }
      }
    }
  });

  it("keeps Baseline fan-out distributed, S3-backed and runtime bounded", () => {
    const baseline = JSON.parse(readFileSync(resolve(workflowsRoot, "baseline.asl.json"), "utf8"));
    expect(baseline.States.B5).toMatchObject({
      Type: "Map",
      MaxConcurrencyPath: "$.baselineMapConcurrency",
      ItemReader: { Resource: "arn:aws:states:::s3:getObject" },
      ResultWriter: { Resource: "arn:aws:states:::s3:putObject" },
      ItemProcessor: { ProcessorConfig: { Mode: "DISTRIBUTED", ExecutionType: "STANDARD" } },
    });
    expect(baseline.States.B5.ItemProcessor.States.B5WorkerOutcome).toMatchObject({
      Type: "Choice",
      Choices: [
        {
          Variable: "$._B5.outcome",
          StringEquals: "REDRIVE_REQUIRED",
          Next: "B5WorkerFailed",
        },
      ],
    });
    expect(baseline.States.B5.ItemProcessor.States.B5WorkerFailed.Type).toBe("Fail");
  });

  it("routes Baseline classification to the immutable classification target", () => {
    const baseline = JSON.parse(readFileSync(resolve(workflowsRoot, "baseline.asl.json"), "utf8"));

    expect(baseline.States.B3.Parameters.FunctionName).toBe("${ClassificationAliasArn}");
    expect(baseline.States.B2.Parameters.FunctionName).toBe("${ControlAliasArn}");
    expect(baseline.States.B4.Parameters.FunctionName).toBe("${CoverageAliasArn}");
  });

  it("routes terminal outcomes from the exact final Lambda result", () => {
    const prGate = JSON.parse(readFileSync(resolve(workflowsRoot, "pr-gate.asl.json"), "utf8"));
    const nightly = JSON.parse(readFileSync(resolve(workflowsRoot, "nightly.asl.json"), "utf8"));

    expect(prGate.States.TerminalRoute.Choices[0].Variable).toBe(
      "$._P8.Payload.terminalOutcome",
    );
    expect(nightly.States.TerminalRoute.Choices[0].Variable).toBe(
      "$._N6.Payload.terminalOutcome",
    );
  });

  it("exports D1-D6 for the promotion Lambda without creating a fifth ASL workflow", () => {
    expect(contracts.workflows.DEPLOYMENT.stages.map((stage: any) => stage.stageId)).toEqual([
      "D1",
      "D2",
      "D3",
      "D4",
      "D5",
      "D6",
    ]);
    expect(existsSync(resolve(workflowsRoot, "deployment.asl.json"))).toBe(false);
  });

  it("wires four logged traced Standard state machines to Lambda aliases and SCA", () => {
    const app = new App();
    const config = fixtureConfig();
    const recovery = new RecoveryStack(app, "WorkflowRecovery", { config });
    const network = new NetworkStack(app, "WorkflowNetwork", { config });
    const data = new DataStack(app, "WorkflowData", { config, network, recovery });
    const engines = new EnginesStack(app, "WorkflowEngines", { config, network, data });
    const runtime = new RuntimeStack(app, "WorkflowRuntime", { config, network, data });
    const publication = new PublicationStack(app, "WorkflowPublication", {
      config,
      network,
      data,
    });
    const orchestration = new OrchestrationStack(app, "WorkflowOrchestration", {
      config,
      network,
      data,
      engines,
      runtime,
      publication,
    });
    const template = Template.fromStack(orchestration).toJSON();
    const machines = Object.values(template.Resources).filter(
      (resource: any) => resource.Type === "AWS::StepFunctions::StateMachine",
    ) as any[];

    expect(machines).toHaveLength(4);
    for (const machine of machines) {
      expect(machine.Properties.StateMachineType).toBe("STANDARD");
      expect(machine.Properties.TracingConfiguration.Enabled).toBe(true);
      expect(machine.Properties.LoggingConfiguration.Level).toBe("ALL");
      const rendered = JSON.stringify(machine.Properties);
      expect(machine.Properties.DefinitionS3Location).toBeDefined();
      expect(rendered).toContain("ControlAliasArn");
      if (machine.Properties.StateMachineName.endsWith("baseline")) {
        expect(rendered).toContain("ClassificationAliasArn");
      }
      expect(rendered).toContain("ScaTaskDefinitionArn");
      expect(rendered).not.toContain("PinnedDefinition");
      const lambdaTargets = JSON.stringify(machine.Properties.DefinitionSubstitutions);
      expect(lambdaTargets).toContain("FunctionCurrentVersion");
      expect(lambdaTargets).not.toContain("LiveAlias");
    }
    const renderedTemplate = JSON.stringify(template);
    expect(renderedTemplate).toContain("lambda:InvokeFunction");
    expect(renderedTemplate).toContain("ecs:RunTask");
    expect(
      Object.values(template.Resources).filter(
        (resource: any) => resource.Type === "AWS::StepFunctions::StateMachineVersion",
      ),
    ).toHaveLength(4);
    expect(
      Object.values(template.Resources).filter(
        (resource: any) => resource.Type === "AWS::StepFunctions::StateMachineAlias",
      ),
    ).toHaveLength(4);
  });

  it("routes all intake lanes to the exact immutable workflow aliases", () => {
    const app = new App();
    const config = fixtureConfig();
    const recovery = new RecoveryStack(app, "IntakeWorkflowRecovery", { config });
    const network = new NetworkStack(app, "IntakeWorkflowNetwork", { config });
    const data = new DataStack(app, "IntakeWorkflowData", { config, network, recovery });
    const engines = new EnginesStack(app, "IntakeWorkflowEngines", { config, network, data });
    const runtime = new RuntimeStack(app, "IntakeWorkflowRuntime", { config, network, data });
    const publication = new PublicationStack(app, "IntakeWorkflowPublication", {
      config,
      network,
      data,
    });
    const orchestration = new OrchestrationStack(app, "IntakeWorkflowOrchestration", {
      config,
      network,
      data,
      engines,
      runtime,
      publication,
    });
    const intake = new IntakeStack(app, "IntakeWorkflowIntake", {
      config,
      network,
      data,
      orchestration,
    });
    const template = Template.fromStack(intake).toJSON();

    const eventSourceMappings = Object.values(template.Resources).filter(
      (resource: any) => resource.Type === "AWS::Lambda::EventSourceMapping",
    ) as any[];
    expect(eventSourceMappings).toHaveLength(3);
    const laneConcurrency = Object.fromEntries(
      eventSourceMappings.map((mapping) => {
        const source = JSON.stringify(mapping.Properties.EventSourceArn);
        const lane = ["interactive", "events", "batch"].find((name) => source.includes(name));
        return [lane, mapping.Properties.ScalingConfig.MaximumConcurrency];
      }),
    );
    expect(laneConcurrency).toEqual({ interactive: 12, events: 6, batch: 2 });
    expect(
      Object.values(template.Resources).filter(
        (resource: any) => resource.Type === "AWS::Events::Rule",
      ),
    ).toHaveLength(4);
    const rendered = JSON.stringify(template);
    expect(rendered).toContain("LINEAGE_BASELINE_WORKFLOW_ALIAS_ARN");
    expect(rendered).toContain("LINEAGE_INCREMENTAL_WORKFLOW_ALIAS_ARN");
    expect(rendered).toContain("LINEAGE_PR_GATE_WORKFLOW_ALIAS_ARN");
    expect(rendered).toContain("LINEAGE_NIGHTLY_WORKFLOW_ALIAS_ARN");
    expect(rendered).toContain("ReportBatchItemFailures");
    expect(rendered).toContain("states:StartExecution");
    const intakeFunction = Object.values(template.Resources).find(
      (resource: any) => resource.Type === "AWS::Lambda::Function",
    ) as any;
    const startPolicy = Object.values(template.Resources)
      .filter((resource: any) => resource.Type === "AWS::IAM::Policy")
      .flatMap((resource: any) => resource.Properties.PolicyDocument.Statement)
      .find((statement: any) => statement.Action === "states:StartExecution");
    const configuredAliases = [
      "LINEAGE_BASELINE_WORKFLOW_ALIAS_ARN",
      "LINEAGE_INCREMENTAL_WORKFLOW_ALIAS_ARN",
      "LINEAGE_PR_GATE_WORKFLOW_ALIAS_ARN",
      "LINEAGE_NIGHTLY_WORKFLOW_ALIAS_ARN",
    ].map((name) => JSON.stringify(intakeFunction.Properties.Environment.Variables[name]));
    expect(new Set(startPolicy.Resource.map((resource: any) => JSON.stringify(resource)))).toEqual(
      new Set(configuredAliases),
    );
    const intakePolicyStatements = Object.values(template.Resources)
      .filter((resource: any) => resource.Type === "AWS::IAM::Policy")
      .flatMap((resource: any) => resource.Properties.PolicyDocument.Statement);
    const intakeDynamo = intakePolicyStatements.find((statement: any) =>
      JSON.stringify(statement.Action).includes("dynamodb:GetItem"),
    );
    expect(JSON.stringify(intakeDynamo.Resource)).toContain("LedgerTable");
  });
});
