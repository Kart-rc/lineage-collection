# `infra` — AWS topology, packaging and generated workflows

The cloud shape of the platform: CDK stacks, deterministic OCI packaging for the
Lambda and Fargate images, and the Step Functions state machines generated from the
Python workflow definitions.

Nothing here is required to run the platform locally. `make dev` needs no AWS
credentials. This workspace exists so the deployed topology can be asserted, synthed
and packaged offline, and deployed to a strictly bounded ephemeral account when
someone explicitly asks for it.

---

## Stacks

Composed by `bin/lineage-platform.ts`. Each is independently addressable so blast
radius stays small.

| Stack | Responsibility |
|---|---|
| `network-stack` | VPC, subnets, PrivateLink endpoints |
| `data-stack` | DynamoDB control tables, versioned and encrypted S3 buckets |
| `intake-stack` | Event receipt, FIFO and batch SQS lanes, dead-letter queues |
| `engines-stack` | Lambda handlers for the workflow stages |
| `orchestration-stack` | The four Step Functions state machines, with versions and live aliases |
| `runtime-stack` | Kinesis stream for runtime observations |
| `publication-stack` | Neptune projection and the fenced pointer |
| `api-stack` | API Gateway and the versioned product-API Lambda with canary aliases |
| `web-stack` | Static hosting for the control room |
| `operations-stack` | Alarms, dashboards, paging topic wiring |
| `recovery-stack` | Cross-region replication and recovery posture |

---

## Generated workflows — do not hand-edit

`apps/api/src/lineage_api/application/workflows/definitions.py` is the **single
authority** for the workflow stages. The ASL files here are deterministic exports of
it.

```bash
make workflow-export   # regenerate from the Python definitions
make workflow-check    # fail if the checked-in ASL has drifted
```

`workflow-check` fails when B1–B10, I1–I10, P1–P8, N1–N6, D1–D6, timeouts, attempt
counts, terminal states or the generated ASL diverge from the Python source. Editing
`baseline.asl.json` by hand will be reverted by the next export and caught by CI.

Four state machines are generated: `baseline`, `incremental`, `nightly` and `pr-gate`.

> **Deployment (D1–D6) has no state machine.** It is defined in Python like the
> others but runs as a synchronous loop inside a single `deployment` Lambda, and no
> EventBridge rule, API Gateway route or Function URL wires it up in these stacks. In
> practice only the local `POST /api/deployments/outcomes` endpoint reaches it today.

Static analysis runs at B5, I5 and N2 as an ECS Fargate task via
`ecs:runTask.waitForTaskToken` with a 300-second heartbeat, not as a Lambda.

---

## Commands

All are run from the repository root.

```bash
make synth           # offline CDK assembly into infra/cdk.out — no credentials needed
make package-aws     # Python wheel + linux/amd64 Lambda and linux/arm64 SCA OCI archives
npm test --workspace infra    # CDK assertions
npm run build --workspace infra  # strict type-check, no emit
```

`make package-aws` needs Docker Desktop. Verify the resulting image digests and the
handler inventory in `infra/dist/runtime-build-metadata.json` and
`infra/dist/package-manifest.json`.

`cdk.out/` and `dist/` are generated. Do not commit them or treat them as source.

---

## Ephemeral AWS deployment

No AWS command runs implicitly, and there is no default account, region or namespace.
`scripts/deploy_ephemeral_aws.sh` requires an explicit account, primary and recovery
regions, three availability zones, a `lineage-e2e-*` namespace, approved PrivateLink
and paging values, a clean commit, and `ALLOW_LINEAGE_EPHEMERAL_AWS=1`.

```bash
export AWS_PROFILE=<approved-profile>
export AWS_ACCOUNT_ID=<approved-12-digit-account>
export AWS_REGION=<approved-primary-region>
export LINEAGE_SECONDARY_REGION=<approved-recovery-region>
export LINEAGE_PRIMARY_AVAILABILITY_ZONES=<az-a,az-b,az-c>
export LINEAGE_EPHEMERAL_PREFIX=lineage-e2e-<unique-suffix>
export LINEAGE_ENTERPRISE_ENDPOINT=https://<approved-private-dns-name>
export LINEAGE_ENTERPRISE_ENDPOINT_SERVICE_NAME=<approved-vpce-service-name>
export LINEAGE_PAGING_TOPIC_ARN=<approved-sns-topic-arn>
export ALLOW_LINEAGE_EPHEMERAL_AWS=1
```

```bash
make aws-deploy    # packages exact OCI digests, creates repositories, then deploys
make aws-smoke     # needs ALLOW_LINEAGE_EPHEMERAL_AWS_SMOKE=1
make aws-cleanup   # needs ALLOW_LINEAGE_EPHEMERAL_AWS_CLEANUP=DESTROY
```

The angle-bracket values above are labels, not defaults — supply them from approved
account context.

`aws-smoke` seeds a checksummed Incremental input and verifies I1–I10, the DynamoDB
and S3 evidence, CloudWatch correlation, and duplicate no-effect.

`aws-cleanup` deletes **only** the validated namespace in the validated account and
regions. Production mode stays deletion-protected and Object-Locked; only the strict
ephemeral mode is disposable. Without approved credentials and opt-ins, the correct
outcome is `AWS_REQUIRED` — not a partial deployment.

---

## Local-to-AWS adapter mapping

One Python package, two deployment shapes, joined by the ports in
`apps/api/src/lineage_api/application/ports.py`.

| Concern | Local | AWS |
|---|---|---|
| Commands, leases, stage ledger | SQLite transactions | DynamoDB conditional writes with lease epochs |
| Queue lanes | SQLite broker, FIFO groups | FIFO + batch SQS, DLQs, partial-batch redrive |
| Workflow | In-process Python | Four versioned Standard Step Functions aliases |
| Static analysis | Local Python module | ARM64 Fargate task with callback token and heartbeat |
| Evidence and packages | Write-once checksummed files | Versioned encrypted S3; production adds Object Lock |
| Runtime observations | Closed-schema session store | Kinesis, same validation contracts |
| Projection and pointer | Versioned SQLite graph | Neptune merge plus a DynamoDB fenced pointer |
| Product API | FastAPI | Versioned Lambda images behind API Gateway |

The local implementation proves behaviour and recovery; CDK assertions and offline
synth prove topology. A real deployment, canary, scale window and recovery drill
remain separate evidence gates that this workspace does not claim to satisfy.

---

## Related

- [Repository overview and navigation](../docs/NAVIGATION.md)
- [Backend API](../apps/api/README.md)
- [Target architecture](../docs/architecture/lineage-platform-target.md)
