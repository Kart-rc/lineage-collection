# B05 — Classifier and workflow orchestrator

Normative requirements: [L03 orchestration](../component-prds/03-orchestration-and-scheduling.md),
[L13 classification](../component-prds/13-repository-classification.md), and
[L15 resilience](../component-prds/15-nfr-and-resiliency-spec.md).

## Ownership

Track B owns classification policy, trigger decisions, workflow definitions, command/lease execution,
coverage completeness, redrive and terminal behavior. Domain engines own their bounded stage results.

## Boundary

Turn one durable command into the exact versioned Baseline, Incremental, PRGate, Nightly or
deployment stage graph. Stages communicate through immutable references and application ports.

## Contracts

Workflow definitions pin stage IDs, attempts, deadlines, retry class, side-effect mode, determinants,
routes and terminal states. Coverage manifests account for every expected scope item and runtime join.

## State and failure model

Commands transition through queued/running/retry/completed/terminal states under lease epochs.
Crashes redrive immutable checkpoints. Unsupported or skipped work is recorded; missing work makes
coverage incomplete. Trigger policy never bypasses review or deployment artifact identity.

## Data ownership

B05 owns classification decisions, commands, attempts, stage identities, coverage manifests and run
ledger. Evidence bodies remain in B03; proposals/publications belong to B11/B12.

## Infrastructure bill of materials

Standard Step Functions for four canonical workflows, dedicated deployment-promotion Lambda,
DynamoDB control tables, Lambda/ECS targets, EventBridge schedules/rules, CloudWatch logs/traces,
alarms and execution roles with pinned aliases.

## Local adapter

The API/worker executes the same definitions using SQLite leases/checkpoints, files, local broker,
fake clocks and named failpoints. It is a correctness oracle, not a Step Functions emulator.

## Security and privacy

Stage roles receive only required references/resources; state contains no source bodies or secrets.
Trigger and re-drive actions are authenticated, authorized, correlated and audited.

## SLOs

Every accepted command terminates visibly; retries/deadlines/fan-out are bounded. Workflow-specific
latency/throughput targets remain release evidence, not inferred from local speed.

## Observability

Expose workflow/stage latency, oldest command, retries, lease steals, deadline/terminal errors,
coverage states, trigger reasons, redrive reuse and end-to-end correlation.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B05-AC-001 | L03/L13 deterministic redrive and completeness | Kill after every stage side effect; redrive converges to identical references, manifests and terminal state; unaccounted scope cannot be COMPLETE | Every PR fault matrix |

## Deployment and rollback

Export definitions from code, reject drift, publish immutable state-machine versions and route new
executions through aliases. In-flight executions remain pinned; rollback restores alias/routing.

## Dependencies

B01 contracts, B03 stores, B04 commands/lanes, B06-B13 stage ports. Repository conventions and
schedule/policy ownership remain CTX seams.

## Definition of Ready

Definition version, every route/error, timeout/retry/fan-out limit, determinant, owner and test oracle
are explicit.

## Definition of Done

Local and ASL definitions have parity; all branches terminate; fault redrive and coverage arithmetic
pass; alarms/runbooks and `B05-AC-001` evidence exist.
