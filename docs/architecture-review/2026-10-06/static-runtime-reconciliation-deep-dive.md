# How static and runtime lineage reconciliation works

Technical deep dive • 6 October 2026

## The answer in one minute

Reconciliation turns several descriptions of a data flow into a scoped, explainable conclusion. Static analysis says what a particular program definition could do. Runtime evidence says what a particular observation mechanism captured during a particular execution. A reconciler determines whether those descriptions concern the same operation, fields, deployed code, schemas and transformation. It then records agreement, disagreement, missing evidence or a newly discovered candidate, with the evidence that justified the decision.

The central rule is: **agreement is meaningful only after identity, context and evidence strength match.** Seeing a database read does not establish which response field used a column. Seeing a mapping annotation execute does not establish that its transformation is correct. Failing to observe an edge does not establish that the edge is impossible.

In the audited `lineage-collection` implementation, reconciliation chiefly strengthens existing claims when runtime observations match them. It already executes real selected code and accepts externally produced observations. Its normal reconciliation paths do not yet provide the full discovery, contradiction and revision-aware lifecycle described here. The distinction matters because a plausible graph and a well-supported graph can look identical.

### Reading map

- For the mental model and executed Petclinic example, read sections 1–2.
- For the proposed four-hop dataset and field walkthrough, including synthetic values and cumulative graphs, read section 2A.
- For the exact algorithm, read sections 3–7.
- For current repository behavior, read section 8.
- For event ordering, replay, retraction and operating the system, read sections 9–10.
- For instrumentation and acceptance tests, read sections 11–12.
- For implementation priorities and terminology, read sections 13–14.

### Evidence boundary

**Implemented** means source inspected at application commit [`32958a35cdf49341b15a4d6cb05f596811396b06`](https://github.com/Kart-rc/lineage-collection/tree/32958a35cdf49341b15a4d6cb05f596811396b06). **Executed experiment** means the separately published [Petclinic experiment](https://github.com/Kart-rc/lineage-collection/blob/dba9967e17caf6343cddd5bee48614c0931a861f/docs/architecture-review/2026-10-06/petclinic-experiment/README.md), whose source, evidence and limits are pinned at publication commit `dba9967e17caf6343cddd5bee48614c0931a861f`. **Proposed** means the recommended design in this document. Examples and pseudocode are explanatory contracts, not existing API payloads or a claim of completed implementation. No application changes or deployment were performed to create this document. The new four-hop walkthrough reports no application test execution; document and HTML checks validate only the explanatory artifacts.

## 1 The three things being reconciled

Keep an immutable observation, an interpreted claim and a published graph edge separate.

1. **Observation:** an actual captured record, such as a JDBC span, Spark plan, mapping event or source file. Preserve its identity, checksum, producer, capture time and original context.
2. **Claim:** an interpretation, such as “this field can contribute to that field” or “this mapping was declared during run R.” Record which extractor and rules produced it.
3. **Decision:** a versioned assessment of comparable claims and counterevidence. It explains what can appear in a particular graph view.

A source file can support a definition claim without any execution. A JDBC observation can support an access claim without any field mapping. A mapping event may support a declared field relation, while independent output tests provide separate, bounded semantic validation. None of these records should silently replace the others.

### Two graph views answer different questions

**Definition lineage** asks, “What dependencies are possible for this artifact and configuration?” It retains unexercised branches and is useful for change impact. Its scope includes source/build revision, schema bindings and relevant configuration.

**Execution lineage** asks, “What do we have evidence for in this execution attempt?” It includes run identity, actual source intervals or snapshots, outcomes and capture limits. It is useful for incident investigation and explaining a particular output.

Reconciliation links these views. It does not force them to become the same graph. One run exercises a subset of possibilities; several runs may exercise different branches. A failed write may appear as an attempted operation without becoming a committed-output dependency.

Also keep separate dimensions for:

| Dimension | Question answered |
|---|---|
| Identity match | Are these records about the same assets and operation? |
| Evidence class | Was the relation inferred, declared, engine-derived or independently tested? |
| Semantic assessment | Do the available checks support this transformation? |
| Capture completeness | Did required evidence arrive for the declared observation scope? |
| Execution outcome | Did the operation succeed, fail, roll back or remain unknown? |
| Publication state | Is this a candidate, accepted claim, conflict or historical claim? |

Compressing all six into a number called confidence loses information that users need.

## 2 A running example from Petclinic

The experiment exercised the real Spring application and H2 database through HTTP. Its bounded target was the owner-summary table on the owner-details page. The Name cell renders:

```text
HTML_TEXT(first_name + " " + last_name)
```

Two value dependencies feed one transformation. A separate `owners.id = request.ownerId` predicate chooses the row. ID influences which name appears, but contributes no characters to the Name cell.

```text
owners.first_name ──VALUE──┐
                         ├── concatenate then HTML render ──> owner-summary.name
owners.last_name  ──VALUE──┘

owners.id ──FILTER──> row selection ──> which owner-summary.name appears
```

The official source is pinned at [`500158f732419217507c7656904b8e6aa1bcc0d6`](https://github.com/spring-projects/spring-petclinic/tree/500158f732419217507c7656904b8e6aa1bcc0d6). The independent gold inspected source, schema, templates and fixtures before inspecting instrumentation or its output. It specified five value edges and four filter influences across the four summary fields. The compound name mapping accounts for two of those value edges. [Gold scope and expected graph](https://github.com/Kart-rc/lineage-collection/blob/dba9967e17caf6343cddd5bee48614c0931a861f/docs/architecture-review/2026-10-06/petclinic-experiment/gold/scope_contract.md).

The experiment established three useful facts:

- Standard OTel auto-instrumentation captured HTTP/JDBC activity but supplied no accepted field mappings in this slice.
- A metadata-only interceptor added a mapping declaration during execution; its five value edges and four normalized filter influences matched gold.
- A real template mutation rendered first name only. The unchanged declaration still matched gold, but independent DOM assertions failed for both owners.

That last result is the reason reconciliation needs counterevidence. A declaration comparison can pass while deployed behavior violates the declared contract. The experiment preserved `DECLARED_AND_EXECUTED` and did not rewrite the sensor facet into proof of field causality. It separately recorded output validation. This prototype did not integrate with the `lineage-collection` backend, and it did not execute Kafka or Spark. [Recorded experiment and limits](https://github.com/Kart-rc/lineage-collection/blob/dba9967e17caf6343cddd5bee48614c0931a861f/docs/architecture-review/2026-10-06/petclinic-experiment/README.md).

## 2A Proposed four hop walkthrough

This additional walkthrough overlays the reconciliation model on a **proposed, illustrative pipeline**. It does not describe implemented or tested behavior in `lineage-collection`, and it is separate from the executed Petclinic experiment in section 2. The companion HTML provides the same data and mappings as interactive, synthetic views. Its evidence controls simulate decisions; they do not collect telemetry or execute the application.

### Scope and data assumptions

There are **four hops and five datasets**:

```text
orders
  ──[1 Pricing service / priceOrder]──> priced_order_payload
  ──[2 Producer / serialize and publish]──> Kafka/order-priced
  ──[3 Consumer / normalize and commit]──> normalized_orders
  ──[4 Spark job / dailyRevenue]──> daily_revenue
```

The pricing service, producer, consumer and Spark job are **operations** connecting datasets. `priced_order_payload` is an explicitly opted-in, versioned logical contract modeled as a dataset; it is not an automatically promoted object allocation. An in-memory variable becomes a lineage dataset only under this governance choice. The four-hop count follows that chosen boundary. Operation nodes can also appear in a richer graph without becoming additional datasets.

All values below are synthetic. Assume USD only; exact decimal arithmetic; non-null valid inputs; UTC timestamps and UTC date grouping; one immutable order and one successful processing. This example excludes currency conversion, updates, retries/replay, joins and deduplication. Those require extra semantics and tests. `order_id` is carried for identity but does not affect the revenue sum under these assumptions. Production lineage telemetry needs identifiers, schemas, typed mappings and evidence references, **not raw business row values**.

The monetary logical type is decimal with two fractional digits. For the illustrative JSON wire contract `price-json/1`, a decimal is encoded as a string such as `"90.00"`; the consumer decodes it exactly to a decimal. This encoding choice is part of the schema contract, not an assertion about an existing Kafka deployment.

### Canonical identities and context

These identities and version labels are fictional examples, not resolved deployment facts. A real reconciler must bind actual namespaces, schemas/fingerprints, deployed build digests, operations and run/commit context before promoting support.

| Dataset | Kind | Illustrative canonical identity | Contract/schema |
|---|---|---|---|
| `orders` | Source table | `postgres://demo-commerce/commerce.public.orders` | `orders/1` |
| `priced_order_payload` | Opt-in logical contract | `logical://demo/priced_order_payload` | `priced-order/1` |
| `Kafka/order-priced` | Kafka topic value | `kafka://demo-cluster/order-priced` | `order-priced-value/1 · price-json/1` |
| `normalized_orders` | Committed table | `postgres://demo-analytics/analytics.public.normalized_orders` | `normalized-orders/1` |
| `daily_revenue` | Aggregate table | `table://demo-warehouse/analytics.daily_revenue` | `daily-revenue/1` |

A service name or class suffix such as `V3` is insufficient. Build, logical payload contract, Kafka writer/reader schema and table schema are independent version dimensions. A runtime record from the wrong build, schema or operation remains **UNKNOWN_CONTEXT** for the selected claim; matching names cannot repair the mismatch.

### Source row before hop one

```json
{
  "order_id": 42,
  "amount_cents": 10000,
  "discount_cents": 1000,
  "status": "PAID",
  "created_at": "2026-10-06T10:00:00Z"
}
```

No output-field lineage has yet been added. The initial dataset graph contains only `orders`; its source fields are the starting points.

### Hop 1 price the order

**Operation:** Pricing service / priceOrder. **Dataset edge:** `orders → priced_order_payload`.

**Transformation:** `netAmount = (amount_cents - discount_cents) / 100.00`.

**Input values**

```json
{
  "order_id": 42,
  "amount_cents": 10000,
  "discount_cents": 1000,
  "status": "PAID",
  "created_at": "2026-10-06T10:00:00Z"
}
```

**Output values**

```json
{
  "orderId": 42,
  "netAmount": "90.00",
  "status": "PAID",
  "occurredAt": "2026-10-06T10:00:00Z"
}
```

**New field relationships at this hop**

| Input field | Output field or scope | Relation | Transformation |
|---|---|---|---|
| `orders.order_id` | `priced_order_payload.orderId` | DIRECT / IDENTITY | `value-preserving mapping` |
| `orders.amount_cents` | `priced_order_payload.netAmount` | DIRECT / TRANSFORMATION | `(amount_cents - discount_cents) / 100.00` |
| `orders.discount_cents` | `priced_order_payload.netAmount` | DIRECT / TRANSFORMATION | `(amount_cents - discount_cents) / 100.00` |
| `orders.status` | `priced_order_payload.status` | DIRECT / IDENTITY | `value-preserving mapping` |
| `orders.created_at` | `priced_order_payload.occurredAt` | DIRECT / IDENTITY | `value-preserving mapping` |

**Static intended mapping.** A supported parser resolves the query/repository, entity-to-column symbols, mapper assignments and decimal expression. Both amount_cents and discount_cents are direct operands; naming similarity is not enough. This is the intended mapping, not a claim about the current parser implementation.

**Observed runtime boundary.** OTel HTTP/JDBC and mapper-completion boundaries can show the read and method execution. They cannot establish the object-field mapping. Reliable custom OTel mapping instrumentation may record the declared contract for this invocation; use an SDK only where that customization is infeasible.

**Executed declaration versus independently tested semantics.** A mapping event may establish `DECLARED_AND_EXECUTED` for its bound invocation, not independently verified field causality. An independently authored fixture and gold graph must assert 42, 90.00, PAID and the preserved UTC timestamp. A drop-discount mutation must produce 100.00 and fail the expected 90.00 check, even when a stale declaration still names both inputs. These are proposed acceptance checks, not reported application test results.

**Context binding.** Bind deployed pricing build digest, source-map revision, priceOrder operation, source table/schema orders/1, logical contract priced-order/1, invocation/run and successful read/mapper completion. V3 in a class name is not an artifact digest.

**Reconciliation.** Evaluate each typed edge and transformation independently. With static analysis plus boundary spans alone, retain the static claim but mark its run-specific field mapping unknown. A matching, context-bound declaration supports only `DECLARED_AND_EXECUTED`; independently authored, discriminating tests add separate bounded semantic support. Missing evidence does not delete the definition; an observed, applicable mismatch is a conflict.

**Caveat.** Reaching the mapper or emitting its declaration is not independent semantic validation. The logical payload is an explicitly governed contract dataset; ordinary temporary objects are not automatically datasets.

**Cumulative dataset graph after hop 1**

```text
orders → priced_order_payload
```

**Cumulative field graph after hop 1**

```text
orders.order_id --DIRECT/IDENTITY--> priced_order_payload.orderId
orders.amount_cents --DIRECT/TRANSFORMATION--> priced_order_payload.netAmount
orders.discount_cents --DIRECT/TRANSFORMATION--> priced_order_payload.netAmount
orders.status --DIRECT/IDENTITY--> priced_order_payload.status
orders.created_at --DIRECT/IDENTITY--> priced_order_payload.occurredAt
```

### Hop 2 publish the payload

**Operation:** Producer / serialize and publish. **Dataset edge:** `priced_order_payload → Kafka/order-priced`.

**Transformation:** `Serialize four fields without changing their logical values`.

**Input values**

```json
{
  "orderId": 42,
  "netAmount": "90.00",
  "status": "PAID",
  "occurredAt": "2026-10-06T10:00:00Z"
}
```

**Output values**

```json
{
  "orderId": 42,
  "netAmount": "90.00",
  "status": "PAID",
  "occurredAt": "2026-10-06T10:00:00Z"
}
```

**New field relationships at this hop**

| Input field | Output field or scope | Relation | Transformation |
|---|---|---|---|
| `priced_order_payload.orderId` | `Kafka/order-priced.orderId` | DIRECT / IDENTITY | `schema-bound serialization; same logical value` |
| `priced_order_payload.netAmount` | `Kafka/order-priced.netAmount` | DIRECT / IDENTITY | `schema-bound serialization; same logical value` |
| `priced_order_payload.status` | `Kafka/order-priced.status` | DIRECT / IDENTITY | `schema-bound serialization; same logical value` |
| `priced_order_payload.occurredAt` | `Kafka/order-priced.occurredAt` | DIRECT / IDENTITY | `schema-bound serialization; same logical value` |

**Static intended mapping.** Resolve the producer call, serializer configuration, wire-schema subject/version and field paths. Map each logical payload field to its wire counterpart. A DTO name or a service class labeled V3 does not resolve a topic or schema.

**Observed runtime boundary.** A producer span plus acknowledgement can support publication to the observed cluster/topic/partition/offset. Neither the acknowledgement nor trace propagation proves field equivalence. Custom serializer/mapping evidence can declare the executed binding; independently validate round-trip semantics.

**Executed declaration versus independently tested semantics.** A mapping event may establish `DECLARED_AND_EXECUTED` for its bound invocation, not independently verified field causality. In an isolated real test, inspect the acknowledged topic record and decode it with the pinned schema. Assert all four fields, including exact decimal 90.00 and timestamp preservation, against gold authored independently of the mapper declaration. These are proposed acceptance checks, not reported application test results.

**Context binding.** Bind pricing build and serializer digest, producer operation, actual Kafka cluster/topic, writer schema fingerprint/version and encoding price-json/1, message identity, acknowledgement and the payload contract. The identifiers shown here are fictional placeholders.

**Reconciliation.** Evaluate each typed edge and transformation independently. With static analysis plus boundary spans alone, retain the static claim but mark its run-specific field mapping unknown. A matching, context-bound declaration supports only `DECLARED_AND_EXECUTED`; independently authored, discriminating tests add separate bounded semantic support. Missing evidence does not delete the definition; an observed, applicable mismatch is a conflict.

**Caveat.** The example JSON uses a decimal string for netAmount under the explicit price-json/1 wire contract, not binary floating-point. Same field spelling across two schemas is not transport proof.

**Cumulative dataset graph after hop 2**

```text
orders → priced_order_payload → Kafka/order-priced
```

**Cumulative field graph after hop 2**

```text
orders.order_id --DIRECT/IDENTITY--> priced_order_payload.orderId
orders.amount_cents --DIRECT/TRANSFORMATION--> priced_order_payload.netAmount
orders.discount_cents --DIRECT/TRANSFORMATION--> priced_order_payload.netAmount
orders.status --DIRECT/IDENTITY--> priced_order_payload.status
orders.created_at --DIRECT/IDENTITY--> priced_order_payload.occurredAt
priced_order_payload.orderId --DIRECT/IDENTITY--> Kafka/order-priced.orderId
priced_order_payload.netAmount --DIRECT/IDENTITY--> Kafka/order-priced.netAmount
priced_order_payload.status --DIRECT/IDENTITY--> Kafka/order-priced.status
priced_order_payload.occurredAt --DIRECT/IDENTITY--> Kafka/order-priced.occurredAt
```

### Hop 3 normalize the event

**Operation:** Consumer / normalize and commit. **Dataset edge:** `Kafka/order-priced → normalized_orders`.

**Transformation:** `orderId → order_id; netAmount → net_amount; occurredAt → occurred_at; status unchanged`.

**Input values**

```json
{
  "orderId": 42,
  "netAmount": "90.00",
  "status": "PAID",
  "occurredAt": "2026-10-06T10:00:00Z"
}
```

**Output values**

```json
{
  "order_id": 42,
  "net_amount": "90.00",
  "status": "PAID",
  "occurred_at": "2026-10-06T10:00:00Z"
}
```

**New field relationships at this hop**

| Input field | Output field or scope | Relation | Transformation |
|---|---|---|---|
| `Kafka/order-priced.orderId` | `normalized_orders.order_id` | DIRECT / IDENTITY | `schema-bound rename/copy; same logical value` |
| `Kafka/order-priced.netAmount` | `normalized_orders.net_amount` | DIRECT / IDENTITY | `schema-bound rename/copy; same logical value` |
| `Kafka/order-priced.status` | `normalized_orders.status` | DIRECT / IDENTITY | `schema-bound rename/copy; same logical value` |
| `Kafka/order-priced.occurredAt` | `normalized_orders.occurred_at` | DIRECT / IDENTITY | `schema-bound rename/copy; same logical value` |

**Static intended mapping.** Resolve deserializer reader/writer schemas, consumer field reads, assignments and sink entity/table mappings. Three renames and one same-name copy preserve logical values. Capture the transaction/write path without inventing record correlation.

**Observed runtime boundary.** A receive/process span and database write span observe boundaries. A successful table commit must be correlated to the consumed message or bounded offset set, consumer attempt and mapping declaration. Span links alone do not prove row-to-message attribution.

**Executed declaration versus independently tested semantics.** A mapping event may establish `DECLARED_AND_EXECUTED` for its bound invocation, not independently verified field causality. Read the committed normalized row and assert all four mapped fields against the independently specified topic fixture. Check failure-before-commit and retry/replay behavior separately before generalizing beyond this teaching assumption. These are proposed acceptance checks, not reported application test results.

**Context binding.** Bind consumer build digest, normalize operation, Kafka cluster/topic/partition/offset and reader/writer schema fingerprints, attempt/run, sink schema normalized-orders/1 and commit/snapshot. An offset acknowledgement is not automatically the sink commit.

**Reconciliation.** Evaluate each typed edge and transformation independently. With static analysis plus boundary spans alone, retain the static claim but mark its run-specific field mapping unknown. A matching, context-bound declaration supports only `DECLARED_AND_EXECUTED`; independently authored, discriminating tests add separate bounded semantic support. Missing evidence does not delete the definition; an observed, applicable mismatch is a conflict.

**Caveat.** Assume one immutable order and one successful processing here. Retries, duplicate delivery, updates, deduplication and joins are excluded. No exactly-once guarantee is inferred; adding dedup would introduce a separate operation and order_id influence.

**Cumulative dataset graph after hop 3**

```text
orders → priced_order_payload → Kafka/order-priced → normalized_orders
```

**Cumulative field graph after hop 3**

```text
orders.order_id --DIRECT/IDENTITY--> priced_order_payload.orderId
orders.amount_cents --DIRECT/TRANSFORMATION--> priced_order_payload.netAmount
orders.discount_cents --DIRECT/TRANSFORMATION--> priced_order_payload.netAmount
orders.status --DIRECT/IDENTITY--> priced_order_payload.status
orders.created_at --DIRECT/IDENTITY--> priced_order_payload.occurredAt
priced_order_payload.orderId --DIRECT/IDENTITY--> Kafka/order-priced.orderId
priced_order_payload.netAmount --DIRECT/IDENTITY--> Kafka/order-priced.netAmount
priced_order_payload.status --DIRECT/IDENTITY--> Kafka/order-priced.status
priced_order_payload.occurredAt --DIRECT/IDENTITY--> Kafka/order-priced.occurredAt
Kafka/order-priced.orderId --DIRECT/IDENTITY--> normalized_orders.order_id
Kafka/order-priced.netAmount --DIRECT/IDENTITY--> normalized_orders.net_amount
Kafka/order-priced.status --DIRECT/IDENTITY--> normalized_orders.status
Kafka/order-priced.occurredAt --DIRECT/IDENTITY--> normalized_orders.occurred_at
```

### Hop 4 aggregate paid revenue

**Operation:** Spark job / dailyRevenue. **Dataset edge:** `normalized_orders → daily_revenue`.

**Transformation:** `WHERE status = 'PAID'; GROUP BY UTC date(occurred_at); SUM(net_amount)`.

The conceptual Spark SQL fixes the session time zone and decimal input type:

```sql
SET spark.sql.session.timeZone=UTC;
SELECT
  to_date(occurred_at) AS revenue_date,
  SUM(net_amount) AS revenue_usd
FROM normalized_orders
WHERE status = 'PAID'
GROUP BY to_date(occurred_at);
```

`occurred_at` is a parsed UTC timestamp and `net_amount` is an exact decimal. Sink decimal precision and overflow behavior must be specified for a production contract.

**Input values**

```json
{
  "order_id": 42,
  "net_amount": "90.00",
  "status": "PAID",
  "occurred_at": "2026-10-06T10:00:00Z"
}
```

**Output values**

```json
{
  "revenue_date": "2026-10-06",
  "revenue_usd": "90.00"
}
```

**New field relationships at this hop**

| Input field | Output field or scope | Relation | Transformation |
|---|---|---|---|
| `normalized_orders.occurred_at` | `daily_revenue.revenue_date` | DIRECT / TRANSFORMATION | `UTC date(occurred_at)` |
| `normalized_orders.net_amount` | `daily_revenue.revenue_usd` | DIRECT / AGGREGATION | `SUM(net_amount)` |
| `normalized_orders.status` | `daily_revenue.row set` | INDIRECT / FILTER | `status = 'PAID'; affects output-row membership` |
| `normalized_orders.occurred_at` | `daily_revenue.groups` | INDIRECT / GROUP_BY | `UTC date(occurred_at); controls aggregate membership` |

**Static intended mapping.** A supported SQL/Spark analyzer derives occurred_at → revenue_date as DIRECT TRANSFORMATION and net_amount → revenue_usd as DIRECT AGGREGATION. It separately records status as INDIRECT FILTER and occurred_at as INDIRECT GROUP_BY.

**Observed runtime boundary.** Use a Spark-native observed query/logical plan with expression and schema bindings, application/query/run/batch identity, source snapshot and output commit. A COMPLETE event or generic OTel span alone is not evidence of these field transformations.

**Executed declaration versus independently tested semantics.** A mapping event may establish `DECLARED_AND_EXECUTED` for its bound invocation, not independently verified field causality. Independently query the committed sink. The one-row example must yield 2026-10-06 and 90.00. To distinguish SUM from MAX, use PAID 90.00 and 50.00 in the same UTC day plus UNPAID 30.00: SUM = 140.00, MAX = 90.00; the unpaid row is excluded. These are proposed acceptance checks, not reported application test results.

**Context binding.** Bind deployed Spark code and plan/expression fingerprint, dailyRevenue operation, UTC session configuration, decimal types, source snapshot/schema normalized-orders/1, sink schema daily-revenue/1 and commit. Compare plan semantics as well as endpoints.

**Reconciliation.** Evaluate each typed edge and transformation independently. With static analysis plus boundary spans alone, retain the static claim but mark its run-specific field mapping unknown. A matching, context-bound declaration supports only `DECLARED_AND_EXECUTED`; independently authored, discriminating tests add separate bounded semantic support. Missing evidence does not delete the definition; an observed, applicable mismatch is a conflict.

**Caveat.** status affects selection, not arithmetic value. occurred_at supplies the date and separately controls grouping. order_id does not affect the sum in this example because no join or deduplication operation is modeled.

**Cumulative dataset graph after hop 4**

```text
orders → priced_order_payload → Kafka/order-priced → normalized_orders → daily_revenue
```

**Cumulative field graph after hop 4**

```text
orders.order_id --DIRECT/IDENTITY--> priced_order_payload.orderId
orders.amount_cents --DIRECT/TRANSFORMATION--> priced_order_payload.netAmount
orders.discount_cents --DIRECT/TRANSFORMATION--> priced_order_payload.netAmount
orders.status --DIRECT/IDENTITY--> priced_order_payload.status
orders.created_at --DIRECT/IDENTITY--> priced_order_payload.occurredAt
priced_order_payload.orderId --DIRECT/IDENTITY--> Kafka/order-priced.orderId
priced_order_payload.netAmount --DIRECT/IDENTITY--> Kafka/order-priced.netAmount
priced_order_payload.status --DIRECT/IDENTITY--> Kafka/order-priced.status
priced_order_payload.occurredAt --DIRECT/IDENTITY--> Kafka/order-priced.occurredAt
Kafka/order-priced.orderId --DIRECT/IDENTITY--> normalized_orders.order_id
Kafka/order-priced.netAmount --DIRECT/IDENTITY--> normalized_orders.net_amount
Kafka/order-priced.status --DIRECT/IDENTITY--> normalized_orders.status
Kafka/order-priced.occurredAt --DIRECT/IDENTITY--> normalized_orders.occurred_at
normalized_orders.occurred_at --DIRECT/TRANSFORMATION--> daily_revenue.revenue_date
normalized_orders.net_amount --DIRECT/AGGREGATION--> daily_revenue.revenue_usd
normalized_orders.status --INDIRECT/FILTER--> daily_revenue.row set
normalized_orders.occurred_at --INDIRECT/GROUP_BY--> daily_revenue.groups
```

### Read the full graph without confusing influence with value

The direct transitive revenue path is:

```text
daily_revenue.revenue_usd
  ← normalized_orders.net_amount                 [DIRECT AGGREGATION: SUM]
  ← Kafka/order-priced.netAmount                 [DIRECT IDENTITY: rename/decode]
  ← priced_order_payload.netAmount               [DIRECT IDENTITY: serialization]
  ← orders.amount_cents AND orders.discount_cents [DIRECT TRANSFORMATION: subtraction / 100.00]
```

The displayed final value is `90.00 USD` for `2026-10-06`. `status` travels through all four datasets before influencing which rows enter the aggregate. It is an **INDIRECT FILTER**, not an arithmetic value source. `created_at → occurredAt → occurred_at → revenue_date` is a direct timestamp-to-date path; the same `occurred_at` separately has an **INDIRECT GROUP_BY** influence on aggregate membership. A field can legitimately have both direct and indirect roles.

`order_id → orderId → order_id` is preserved through normalization and stops there in this model. It does not contribute to `revenue_usd`. If a later design adds `dropDuplicates(order_id)` or a join, model that operation explicitly with its key/control dependencies and re-test the semantics rather than silently adding an arithmetic edge.

Dataset-wide row selection and grouping can be stored as dataset-level indirect influences, rather than falsely treating `row set` and `groups` as real output columns. Those labels in the diagrams are explanatory scopes. Exact OpenLineage representation depends on the chosen facet/schema and integration version. OpenLineage defines DIRECT identity/transformation/aggregation separately from INDIRECT filter/group-by influences. [Official column-lineage facet documentation](https://openlineage.io/docs/spec/facets/dataset-facets/column_lineage_facet/).

### Per edge evidence and decision ledger

These are proposed local decision labels, not claimed repository API enums or universal OpenLineage statuses. Keep identity/context, evidence strength, semantic test scope and publication state separate.

| Evidence for the same typed edge in the same context | Proposed assessment | What remains unresolved |
|---|---|---|
| Static parser mapping plus HTTP/JDBC/Kafka boundary spans | STATIC_ONLY plus observed boundaries | Runtime field mapping and transformation correctness |
| Static claim plus context-bound custom OTel/SDK mapping event | DECLARED_AND_EXECUTED | The event may repeat the same mistaken declaration |
| Matching engine-native Spark plan | ENGINE_DERIVED support for the bound plan expressions | Arbitrary UDF semantics, capture gaps and sink correctness unless separately established |
| Independently authored gold and discriminating output checks | BOUNDED_TEST_SUPPORT, recorded separately | Only the exercised fixtures/operators/branches are covered |
| Different deployed build, schema or operation, or unresolved identity | UNKNOWN_CONTEXT | Claims are not eligible to corroborate each other |
| Required telemetry absent or capture incomplete | NOT_OBSERVED / UNKNOWN | Absence cannot retract a static definition or prove a field unused |
| Applicable observed expression or independent output contradicts the intended mapping | CONFLICT | Preserve both sides and the affected edge/path; do not overwrite history |

One correct edge does not validate all fields in a hop. A matched dataset boundary does not establish a complete field graph. Unmatched runtime edges remain candidates or unknowns rather than silently disappearing. If a mutation affects net amount, preserve the unaffected ID, status and timestamp mappings instead of marking every field wrong. Source-to-sink confidence cannot exceed unresolved evidence on any required segment.

### Synthetic mutation controls and discriminating fixtures

The HTML can show the baseline, boundary-only, executed-declaration, independently tested fixture, context-mismatch, missing-telemetry and drop-discount conflict scenarios. Every displayed runtime state is a **synthetic simulation**, not actual telemetry or a recorded application execution.

- Baseline price: `(10000 - 1000) / 100.00 = 90.00`.
- Drop-discount mutation: `10000 / 100.00 = 100.00`. A stale mapping declaration can still list `discount_cents`, so declaration agreement alone misses the defect. Independently checking the output against `90.00` reveals the semantic conflict. That conflict affects the net-amount path and its downstream revenue, not the ID/status/time copies.
- A single paid row of `90.00` yields `90.00` under both SUM and MAX. It cannot discriminate those aggregations.
- A separate synthetic fixture uses two PAID rows on the same UTC day, `90.00` and `50.00`, plus an UNPAID row of `30.00`. Correct filtering and SUM produce `140.00`; MAX produces `90.00`. The unpaid `30.00` must be excluded. This fixture is proposed test data and arithmetic, not a report that Kafka/Spark or the repository tests were run.

For a real acceptance test, supply distinct order IDs, exact cents and discounts, fixed UTC timestamps, isolated snapshots/offsets, fresh sink state, an independently reviewed gold graph and a declared completion deadline. Assert semantic output, typed field graph, boundary correlation and capture health independently. Do not derive the gold from the same parser or annotation being evaluated.

### What each sensor is allowed to establish

1. **SCA:** supported syntax plus symbol/entity/schema resolution yields intended field mappings and expressions. Unsupported reflection, serializer behavior, dynamic SQL and UDFs remain explicit gaps.
2. **OTel auto-instrumentation:** request, database, publish/receive and transaction boundaries provide execution context. They do not automatically prove field flow.
3. **Customized OTel:** reliable, completion-aware instrumentation can carry the mapping contract and its actual invocation. Label it as an executed declaration. Do not emit row values.
4. **SDK fallback:** use a separate SDK only where OTel customization is infeasible; it has the same provenance and independent-validation obligations.
5. **Spark-native capture:** observed plan expressions offer engine-derived evidence for supported operators; bind the plan to the source snapshot and sink commit. A declared job completion is not a complete proof.
6. **Independent tests:** add bounded, external semantic evidence. The display of synthetic rows in this document is not such a test result.

This walkthrough adds a concrete explanatory contract. It does not change the repository audit findings, the Petclinic experiment's recorded scope, or the distinction between a proposed reconciler and implemented behavior.

## 3 Stage one ingest and normalize without inventing facts

The proposed pipeline is:

```text
Static sources ─> versioned extraction ─┐
                                      ├─> normalized facts ─> context binding
Runtime capture ─> semantic adapter ───┘           │                 │
                                                  │                 v
Independent tests ─> scoped validation records ───┴─> candidate matching
                                                                    │
                                                                    v
                                               evidence checks and decisions
                                                                    │
                                                                    v
                                             versioned definition and run views
```

Ingestion authenticates the producer, enforces tenant and dataset scope, validates the envelope and durably records the event. Normalization then resolves names and encodes what the event actually means. Authentication binds the accepted credential or principal and its scope; it does not independently establish sensor provenance, truthful instrumentation or the message's lineage interpretation.

Resolve `PUBLIC.OWNERS`, an ORM entity and a JDBC table identifier through a versioned catalog binding. Do not lowercase every name blindly: quoted identifiers and different database rules can make case significant. Include environment and physical system identity so two databases named `petclinic` cannot collide. Treat an in-memory H2 database's lifecycle as execution context even when its logical dataset name stays stable.

Model the rendered output as a logical collection with a schema, here `logical://spring-petclinic` plus `owners/ownerDetails#owner-summary`. Do not mint a dataset for every HTTP request or equate the whole HTML page with the four scoped fields. Preserve explicit logical-to-physical bindings.

### A normalized definition claim

The following JSON is a **proposed illustrative record**, not captured Petclinic SCA output. Symbolic IDs such as `build-A` represent immutable registry references; production records would resolve them to real digests and manifests.

```json
{
  "claimId": "definition-name-v1",
  "tenant": "demo",
  "environment": "isolated-test",
  "operation": "petclinic.GET./owners/{ownerId}.owner-summary",
  "context": {
    "build": "build-A",
    "activeTemplate": "template-T1",
    "inputSchema": "owners-schema-S1",
    "outputSchema": "summary-schema-S1",
    "configuration": "config-C1"
  },
  "mapping": {
    "inputs": [
      {"asset": "h2.petclinic.PUBLIC.OWNERS.first_name", "role": "VALUE"},
      {"asset": "h2.petclinic.PUBLIC.OWNERS.last_name", "role": "VALUE"}
    ],
    "output": "logical.petclinic.owner-summary.name",
    "expression": "HTML_TEXT(CONCAT(first_name, ' ', last_name))"
  },
  "applicability": {
    "predicate": "owner lookup succeeds and owner-summary is rendered",
    "guardRef": "source-path-owner-success"
  },
  "evidenceClass": "STATIC_EXACT_FOR_SUPPORTED_CONSTRUCT",
  "evidenceRefs": ["source-extraction-17"],
  "extractorVersion": "template-parser-v1"
}
```

The mapping groups both inputs. For traversal, it can project into two value edges, but those edges retain the same mapping ID and expression. Otherwise the graph may accidentally imply either input alone is a complete explanation of the output. A separate typed FILTER claim represents `owners.id`.

“Exact” here means exact within supported parser semantics, not universal correctness. Unsupported template expressions, dynamic code or UDF behavior must produce an explicit unresolved record.

### A normalized runtime observation

This is also **proposed illustrative JSON**. It describes the kind of declaration used in the experiment without pretending these short IDs are its captured identifiers.

```json
{
  "eventId": "producer-P:run-R1:event-8",
  "payloadDigest": "payload-digest-8",
  "producer": "petclinic-otel-interceptor-v1",
  "runId": "R1",
  "attemptId": "R1-attempt-1",
  "operation": "petclinic.GET./owners/{ownerId}.owner-summary",
  "context": {
    "build": "build-A",
    "activeTemplate": "template-T1",
    "inputSchema": "owners-schema-S1",
    "outputSchema": "summary-schema-S1",
    "configuration": "config-C1"
  },
  "evidenceClass": "DECLARED_AND_EXECUTED",
  "mappingContract": "petclinic-owner-details/v1",
  "mappingContractDigest": "contract-digest-1",
  "mappingRef": "definition-name-v1",
  "traceSpanRefs": ["http-server-span", "owners-select-span"],
  "operationOutcome": "SUCCEEDED",
  "captureState": "REQUIRED_SENSORS_PRESENT",
  "businessValidationRef": null
}
```

The adapter resolves the versioned contract to its mapping content. The reference cannot be accepted merely because its name matches: validate its digest and bind the deployed template separately. The actual Petclinic mutant used the same application JAR with a different active template. A JAR-only match would miss the material change.

## 4 Stage two bind the evidence to the right context

Before comparing edges, construct a context key:

```text
tenant + environment + operation + deployed artifact set
       + input/output schema bindings + relevant configuration
```

The artifact set can include application JAR, SQL definition, template, UDF, view and model versions. A Git commit alone may not identify deployed behavior. Feature flags, search paths, time zones and serializer versions belong in context when they affect interpretation.

Carry path predicates and applicability conditions with each definition claim, then bind runtime evidence to the path actually exercised where that is known. One operation may use SUM on one branch and MAX on another. Those definitions can coexist without conflict. Unknown path applicability remains unresolved or unexercised; shared operation and output identity cannot turn mutually exclusive possibilities into a semantic contradiction.

For execution lineage, add logical run, attempt, source interval/snapshot and sink outcome. In a streaming chain, this might mean Kafka cluster/topic/partition offsets, reader/writer schemas, Spark query/batch IDs and committed table snapshot. Trace association can link activity; it does not itself prove a field mapping or sink commit.

Use **three identities** rather than one overloaded edge hash:

1. **Logical relation key:** tenant, canonical endpoints, direction, dependency role and operation. Useful for finding comparable relations across revisions.
2. **Scoped definition key:** logical relation plus artifact set, schema/configuration bindings and mapping structure. Different contexts do not share active support automatically.
3. **Evidence identity:** producer/event/checksum and extraction version; runtime evidence also retains its run/attempt. Retries are not additional independent witnesses.

The transform belongs in a claim fingerprint, while a broader comparison key groups alternative transforms for the same mapping. If transform is the only candidate key, `SUM(x)` and `MAX(x)` never meet and cannot be reported as a conflict. If all versions share one unscoped key, old evidence can support new behavior incorrectly.

When context is missing or ambiguous, use `UNRESOLVED_BINDING`. Keep the observation and explain what binding is needed. Do not choose a nearby table or the newest schema because it makes an edge match.

## 5 Stage three find candidates and compare meaning

Candidate generation is an indexed lookup, not a decision. First restrict to authorized scope and comparable contexts. Then use two lookup lanes. An exact-support index finds canonical endpoints, operation and dependency role. A broader discrepancy index groups the same output, operation and context even when input sets, roles or transforms differ. This lets an omitted source or VALUE/FILTER substitution against an expected mapping become an explicit disagreement rather than two unrelated nonmatches. Preserve the complete source set for compound mappings. Assess path applicability before deciding whether alternatives conflict, and retain distinct legitimate mappings to the same output.

### Proposed comparison matrix

| Static claim and runtime evidence | Assessment | Consequence |
|---|---|---|
| Same context, typed endpoints and compatible mapping | Comparable agreement | Add support at the runtime evidence's actual strength |
| Same endpoints, different operation | Different claim | Do not cross-corroborate |
| Same field names, different physical system/schema | Unresolved or different context | Bind explicitly or keep separate |
| Same endpoints, VALUE versus FILTER | Different semantics | Both can coexist; flag a role substitution against the scoped expected mapping |
| Same context and applicable path, independently supported `SUM` versus `MAX` | Semantic conflict | Retain alternatives and counterevidence; block semantic validation |
| Equivalent supported expression forms | Compatible after normalization | Record the normalization rule/version |
| Runtime has `A → C`, static has only `A → B` | Runtime-only candidate | Preserve `A → C`; do not automatically delete `A → B` |
| Static relation absent from one runtime capture | Bounded non-observation | Preserve potential relation; assess coverage separately |
| Runtime has only dataset access or service connectivity | Coarser evidence | Support access/connectivity only, never invent field mapping |
| Runtime belongs to an older deployed revision | Historical support | Attach to its revision; no automatic support carryover |

### Semantic comparison needs more than strings

Normalize only transformations whose meaning the implementation understands: resolved identifiers, AST operator structure, operand order, dialect, types and null behavior. Do not declare expressions equivalent just by removing whitespace or sorting operands. Concatenation is order-sensitive; casts, collation, overflow and decimal rules can matter. Unsupported equivalence is `UNKNOWN`, which is different from `CONFLICT`.

For `SUM(x)` versus `MAX(x)` on the same applicable path, the topology matches but aggregation semantics differ. A runtime plan that actually identifies `MAX` provides stronger counterevidence than a repeated annotation containing `MAX`. A captured analyzed or optimized plan establishes the engine's representation for that execution, not independent tracing of actual row-value causality. Both kinds of evidence should remain visible, with their evidence classes. The reconciler must distinguish a contradictory declaration from independently established behavior. It must also distinguish contradiction from legitimate branch alternatives. Likewise, the same field can be both a value input and a filter input; the presence of both roles is not itself an error.

For `A → B` versus `A → C`, neither edge alone disproves the other. The application may write both, branch on input, or use configuration-specific destinations. A scenario that explicitly requires only B can fail when C appears, but that is a scoped contract violation, not a universal proof that B is impossible.

### Correlated agreement is still correlated

If the static extractor and runtime SQL adapter use the same parser, they can share the same bug. If a generated annotation repeats static output, agreement is even more directly dependent. Record derivation ancestry and a shared-method group, for example parser family/version and source contract. Three copies of one inference are not three independent validations.

Independent gold means the oracle was authored without looking at the predictions under evaluation. It can still rely on source requirements and fixtures; its independence is from the tested extraction path, not an assertion that it is infallible.

## 6 Stage four check evidence and produce a decision

For every candidate, check these gates explicitly:

1. **Admissibility:** authenticated scope, schema-valid envelope, checksum and permitted metadata.
2. **Binding:** artifact set, operation, schemas and run/attempt can be resolved.
3. **Meaning:** the source can support this granularity and dependency type.
4. **Outcome:** execution/commit status is sufficient for the particular claim.
5. **Coverage:** required sensors, capture losses and scenario scope are known.
6. **Counterevidence:** conflicting comparable observations and independent validations are included.

These gates support different decisions. Partial capture need not erase a trustworthy positive observation, but it prevents a claim of complete run coverage. A successfully committed output can be known even if another diagnostic span was lost; conversely, a method-return event alone cannot establish commit.

### Suggested decision vocabulary

- `POTENTIAL`: a scoped definition claim with no eligible execution support yet.
- `OBSERVED_CANDIDATE`: a runtime-discovered relation awaiting the required identity/semantic review or policy gate.
- `SUPPORTED`: comparable evidence supports the relation at the stated evidence class.
- `CONFLICTED`: comparable claims or validation results disagree materially.
- `UNRESOLVED`: a required identity or interpretation remains unknown.
- `RETRACTED` or `SUPERSEDED`: a prior derived claim is no longer active in its stated scope.

Keep `UNOBSERVED_IN_RUN` and `CAPTURE_INCOMPLETE` as run-specific annotations, not synonyms for a false definition. Keep confidence, if retained, as a derived label with an explanation. Do not invent a calibrated probability from the number of mechanisms.

### Example decision output

After the enhanced Petclinic-style request and independent name assertion, a **proposed** result could be:

```json
{
  "decisionId": "D1",
  "graphRevision": "G12",
  "policyVersion": "reconcile-v1",
  "claimId": "definition-name-v1",
  "definitionState": "SUPPORTED",
  "runId": "R1",
  "runtimeEvidenceClass": "DECLARED_AND_EXECUTED",
  "topologyAssessment": "MATCH",
  "semanticAssessment": "VALIDATED_FOR_SCENARIO",
  "validationRef": "independent-owner-1-output-check",
  "coverage": {
    "requiredSensors": "PRESENT",
    "endToEndCapture": "NOT_ESTABLISHED"
  },
  "supportRefs": ["source-extraction-17", "producer-P:run-R1:event-8"],
  "counterevidenceRefs": [],
  "reasonCodes": ["EXACT_CONTEXT_BINDING", "DECLARATION_MATCH", "OUTPUT_CHECK_PASS"]
}
```

`VALIDATED_FOR_SCENARIO` does not promote the declaration into an engine observation. It describes a separate validation record whose fixtures, oracle and scope remain inspectable. Matching two names does not establish every Unicode, null, escaping or localization behavior.

## 7 Deterministic reconciliation pseudocode

The following is **proposed**, intentionally separating definition and execution views. Helper functions must be versioned and return structured reasons, not silently guess. Both decision functions enforce all six evidence gates for each witness's own run/attempt. A required sensor from another run cannot fill a local evidence gap. Incomplete capture can preserve an eligible positive fact, but cannot excuse a missing prerequisite for that particular claim.

```python
def reconcile_definitions(scope, snapshot, policy):
    facts = active_facts(snapshot, scope)  # applies scoped supersessions/retractions
    facts = canonical_sort(facts)          # independent of ingestion order
    definitions = definition_claims(facts)
    runtime = runtime_claims(facts)
    results = []

    for group in comparison_groups(definitions, runtime, scope):
        comparable, unresolved = bind_and_partition(group, policy)
        results.extend(unresolved_decisions(unresolved))
        for context_group in comparable:
            # Retain runtime-only groups: static membership is not an ingest gate.
            relations = compare_typed_mappings(
                context_group, policy,
                require_overlapping_applicability=True,
                unknown_applicability="UNRESOLVED")
            validations = applicable_validations(facts, context_group)
            witness_checks = assess_evidence_per_attempt(context_group, facts, policy)
            results.append(decide_definition(
                relations, validations, witness_checks,
                preserve_runtime_evidence_class=True,
                discount_shared_derivation=True,
                absence_is_disproof=False))

    semantic_inputs = fact_set_and_registry_digests(snapshot, scope)
    manifest = content_address(canonical_sort(results), semantic_inputs, policy.version)
    return publish_verified_revision(manifest)


def reconcile_execution(run, snapshot, policy):
    context = trusted_run_binding(run, snapshot)
    evidence = active_attempt_facts(run, snapshot)
    coverage = assess_capture(evidence, expected_sensor_manifest(run))
    observations, gaps = normalize_and_bind(evidence, context, policy)
    definitions = applicable_definitions(context, snapshot)
    results = []

    for observation in canonical_sort(observations):
        candidates = exact_and_discrepancy_candidates(
            definitions, observation.context, observation.operation,
            observation.output, observation.typed_relation)
        candidates = assess_path_applicability(
            candidates, observation.path_evidence, context)
        comparison = compare_or_create_candidate(observation, candidates, policy)
        results.append(decide_execution(
            comparison, observation.outcome,
            applicable_validations(evidence, observation),
            witness_checks=assess_evidence_for_observation(
                observation, evidence, coverage, policy)))

    # Does not remove a definition or assert non-execution.
    results += bounded_non_observations(definitions, observations, coverage)
    results += unresolved_decisions(gaps)
    return persist_run_view(run, results, coverage, snapshot, policy.version)
```

Definition and run decisions derive from source facts and independent validation records; they must not recursively count each other's conclusions as new evidence. A prior graph is a cache/view, not another witness.

Determinism means the same fact set, context registry snapshot and policy versions produce the same semantic result digest. Processing timestamps and audit sequence numbers may differ. Publish the serving pointer only after the revision manifest and every materialized chunk verify. Readers see either the old complete revision or the new one, with explicit gaps, never an accidentally half-written graph.

## 8 What the audited implementation actually does

The existing system has several reconciliation paths. Their matching rules differ, so “runtime verified” needs a path-specific explanation.

| Path | Runtime source and match rule | Important limit |
|---|---|---|
| Generated Python | Executes source with synthetic dataset primitives; compares `(from_urn, to_urn)` | Ignores edge type and transformation in that comparison; normal stage emits matched static edges |
| Generated Java | Executes selected methods with framework stubs/repository proxies; matches table, READ/WRITE and field | Endpoint identity is not part of this matcher; fields are enumerated by the recorder |
| External session ELEMENT | Accepts SDK/OTel/OpenLineage observations; exact canonical endpoints and edge type | Reconciles against existing ledger edges; transform compatibility is not checked there |
| External session DATASET | Dataset identities and edge type | Dataset support does not become element-level confidence |
| Generic assertion merge | Groups supplied assertions by edge key | Can create an entry without SCA, but this is not a wired runtime-discovery flow |

### Generated Python and Java

Python executes actual supplied functions, but its injected reads return field handles rather than real rows. Writes identify field references through substrings in mapping strings; they do not independently evaluate transformation semantics. A runtime observation of A→C can therefore exist even when static analysis only found A→B. The comparison can report runtime-only observations, while the normal runtime stage emits only matched static edges. `CORROBORATED` requires no static-only remainder, not zero runtime-only observations. [Python comparator](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/services/runtime_verification.py#L53-L120), [execution and injected primitives](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/services/runtime_verification.py#L204-L315), [normal stage reduction](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/application/runtime_stage.py#L78-L131).

Java invokes selected source using generated stubs and recording repository proxies. The recorder emits enumerated entity fields on repository calls and returns placeholders. The normal stage matches table, operation and field, then copies the matched static endpoints. An observation from one exercised method can consequently support a different unexercised endpoint with the same table/operation/field. Its normal-stage `runtime_only` count is fixed at zero. [Java matcher](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/application/java_runtime_stage.py#L93-L131), [recorder](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/services/java_runtime_verification.py#L1283-L1322), [stage result](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/application/java_runtime_stage.py#L373-L400).

These are real executions with bounded instrumentation, not a universal independent semantic oracle.

### External observations and the promotion boundary

Signed sessions can accept independently produced observations within authorized dataset/artifact scope, including a relation absent from static analysis. The external reconciler then scans existing edges. ELEMENT matching requires exact endpoints and edge type, including SDK service-endpoint identity. An unknown A→C remains ingested evidence but is not turned into a new edge by this path. [Session ingestion](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/services/runtime.py#L410-L471), [external matcher](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/services/consolidation.py#L238-L341).

Session `COMPLETE` means declared delivery accounting closed drained with matching counts and no recorded rejected/buffered/dropped items. It does not establish branch coverage, a missing sensor's activity or semantic truth. Evidence selection is artifact-scoped, but candidate ledger matching does not itself filter existing edges by artifact revision. [Session closure](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/services/runtime.py#L184-L228), [reconciliation orchestration](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/services/orchestration.py#L1620-L1685).

The generic `merge(assertion)` can create a new ledger entry, and the generic AWS consolidation stage groups supplied assertions without requiring an SCA counterpart. That exception prevents an overbroad claim that runtime-only edges are impossible everywhere. The inspected AWS runtime-validation stage forwards assertion references; it does not implement the missing observation-to-new-assertion discovery path. [Generic merge](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/services/consolidation.py#L184-L222), [generic stage](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/application/stage_handlers.py#L1082-L1147), [runtime-validation stage](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/application/stage_handlers.py#L775-L881).

### Conflict and revision behavior

The current edge key hashes sorted sources, target and edge type. It excludes artifact revision and transformation. Merge appends prior provenance and deduplicates by provenance ID. This supports versioned ledger history, but does not provide the proposed active, revision-scoped evidence lifecycle. Old runtime provenance can influence a later claim with the same edge key. [Key and derivation](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/application/consolidation.py#L20-L94), [ledger merge](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/services/consolidation.py#L184-L222).

Crucially, transform-conflict detection compares SCA and LLM assertions, excluding RUNTIME. A runtime `MAX(x)` can coexist with static `SUM(x)` without this rule flagging their disagreement. Completed ELEMENT runtime adds the runtime mechanism for confidence; SCA plus runtime maps to HIGH. That band is a mechanism-combination rule, not a measured probability that SUM is correct. [Conflict logic](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/application/consolidation.py#L30-L94), [confidence bands](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/domain/confidence.py#L28-L50).

## 9 A worked decision ledger across changing evidence

This table is a **proposed lifecycle walkthrough** using the Petclinic lesson. G1–G8 and event IDs are illustrative, not recorded experiment identifiers. Each row adds evidence; earlier history remains queryable.

| Event | Evidence and context | Derived decision |
|---|---|---|
| G1 | Static name mapping for build A, template T1, schema S1 | POTENTIAL name relation, with two VALUE inputs and separate ID FILTER |
| G2 | R1 captures HTTP/JDBC and matching declaration for A/T1/S1 | SUPPORTED at DECLARED_AND_EXECUTED strength; capture completeness still separate |
| G3 | Independent owner-1 and owner-2 output checks pass for their runs | Add scenario validation; do not relabel declaration as engine-derived |
| G3 again | Identical producer event ID and checksum are replayed | No semantic change and no extra witness |
| G4 | R2 uses template T2 but repeats the T1 mapping contract; independent name check fails | Separate deployed context; flag stale contract and failed validation; preserve T1 support |
| G5 | A lost JDBC span leaves R3's required evidence incomplete | Mark R3 coverage gap; do not retract T1's definition or borrow R1's sensors |
| G6 | Authoritative deployment/schema registry establishes T3/S2 | Create a new context; old S1 evidence remains historical |
| G7 | Late R1 evidence arrives after S2 deployment | Update the applicable S1 historical/run view; no support added to S2 |
| G8 | Adapter defect invalidates one R1 interpreted assertion | Append scoped retraction; recompute affected decisions; keep raw evidence and independent checks |

Two subtleties are essential. First, the T2 mutation need not prove that T1 was wrong: it is a different active template. It does show that the old declaration is not reliable evidence of T2's behavior. Second, retracting one faulty assertion does not necessarily retract the relation. Another valid source may still support it, so decisions must be recomputed from remaining applicable facts.

### Schema evolution and actual removal

Suppose schema S2 replaces `last_name` with `family_name`. A registry migration can establish continuity, but spelling similarity cannot. Record the migration, field-version identity and compatibility rules. Bind old runs to S1 and new runs to S2. If compatibility is uncertain, report an unresolved cross-version boundary.

Definition removal requires a successfully completed, authoritative replacement scope or an explicit scoped tombstone. A partial scan, failed parser or missing file download must not delete old claims. A full new artifact can supersede old active definitions while historical executions remain intact. Distinguish valid time, when a claim applied, from recorded time, when the system learned it. Late evidence changes knowledge without rewriting when the event happened.

This supersession/retraction model is proposed. The current append-and-derive ledger should not be described as already implementing it.

## 10 Making the lifecycle reliable

### Idempotency and ordering

Use `(tenant, producer, eventId)` plus a content checksum. The same ID and checksum is a replay; the same ID with different content is a collision that requires quarantine. Deduplicate graph relations separately from raw event counts. Multiple legitimate observations can support one edge without creating multiple edges or independent confidence votes.

Do not use arrival order or wall-clock timestamps as the sole authority. Retain producer sequence, attempt identity and authoritative revision relationships. A COMPLETE event may arrive before a delayed observation; process it as a lifecycle update and reassess declared counts. Conflicting terminal outcomes require reconciliation, not whichever one arrived last. Retractions identify their target and scope explicitly and remain effective if the target arrives later.

### Replay and correction

Persist raw evidence, normalization version, identity-registry snapshot and reconciliation policy version. A parser fix produces new interpreted assertions and scoped supersessions, without editing the original capture. Replay only the affected partitions, then compare added, removed, changed and unresolved decisions before publication.

An evidence-to-claim reverse index identifies which decisions a correction can affect. Index candidates by tenant/context/endpoints/type rather than scanning the whole graph per observation. Materialization must be idempotent so a crash after graph write but before acknowledgement can safely replay. Keep old revisions queryable for audit and rollback.

### Quarantine and privacy

Quarantine malformed schema, unauthorized scope, checksum collisions and impossible identity bindings with a reason and recovery route. Ordinary semantic disagreement belongs in an explicit conflict record; hiding it as malformed input would conceal important evidence. Missing bindings can remain unresolved pending a registry update. Retrying the same bad payload forever is not recovery.

Collect allowlisted metadata rather than row values, HTTP bodies, SQL literals, credentials or method arguments. Source/field names can themselves be sensitive. Enforce tenant/object authorization before candidate lookup and graph traversal; do not leak hidden asset names through conflict messages or counts. Retention and authorized erasure requirements still apply to evidence storage: “immutable” means corrections are append-only under normal processing, not that privacy obligations disappear.

Production capture should have bounded overhead and observable loss, allowing business execution to continue when telemetry fails. Acceptance environments should fail required evidence gates after a declared completion deadline. Neither behavior changes what the evidence proves.

## 11 Instrumentation and the common contract

Use OTel first for service boundaries and supported customization. The Java agent instruments common libraries and provides a base for application-specific telemetry. [OTel Java agent documentation](https://opentelemetry.io/docs/zero-code/java/agent/).

The proposed collection layout is:

```text
Service auto-instrumentation + metadata-only custom mapping events
                              │ OTLP
                              v
                     semantic adapter
                              │
Fallback lineage SDK ─────────┼──> OpenLineage-compatible fact envelope
                              │                   │
Spark-native OpenLineage ─────┘                   v
                                     reconciliation and serving views
```

Use a separate lineage SDK only where feasible OTel customization cannot supply the required evidence. For Spark, use its native OpenLineage integration and validate supported operations/versions; opaque UDFs and hidden I/O still need explicit gap handling. [Spark integration documentation](https://openlineage.io/docs/integrations/spark/).

All lineage sources should converge on the same identity policy and OpenLineage representation where applicable. Use standard schema and column-lineage facets for their defined meanings, including direct value relationships and indirect filter/group/join influences. Use versioned project-prefixed custom facets for evidence class, artifact binding, observation references, capture loss and unsupported semantics. [Column-lineage facet](https://openlineage.io/docs/spec/facets/dataset-facets/column_lineage_facet/), [custom facets](https://openlineage.io/docs/spec/facets/custom-facets/).

The OTLP adapter performs semantic work: binds service/build identities, selects an operation execution, validates span ancestry and contract digest, resolves schemas and preserves outcomes. Changing an exporter or translating JSON field names cannot supply missing meaning. A declaration remains a declaration after OpenLineage conversion. A schema-valid event can still contain an incorrect mapping.

Static definitions can use appropriate non-run OpenLineage events without inventing an execution. Model a stable operation as a Job and an execution attempt as a Run under an explicit project convention. Preserve asynchronous span links, source intervals and sink commits rather than treating a whole trace as one universal business run. [OpenLineage object model](https://openlineage.io/docs/spec/object-model/).

The audited local OTel route uses a legacy envelope; a richer generic OTLP adapter is separate library code, not established as the normal production path. The proposed service adapter and lifecycle need product integration and acceptance tests. [Configured runtime adapters](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/services/runtime.py#L532-L613), [legacy OTel normalization](https://github.com/Kart-rc/lineage-collection/blob/32958a35cdf49341b15a4d6cb05f596811396b06/apps/api/src/lineage_api/runtime/adapters/otel.py#L372-L408).

## 12 Acceptance tests that establish the right thing

ATDD means specifying the expected behavior and evidence before implementation. Each scenario needs pinned artifacts, schemas, scope, fixtures, an independently authored gold graph and explicit completion conditions. Compare static predictions to gold, runtime facts to gold and reconciliation decisions to expected policy outcomes separately.

### Petclinic scenario

**Given** the pinned application, fresh H2 fixtures, T1 template and a gold contract covering only the four owner-summary cells;

**When** real HTTP requests fetch owners 1 and 2;

**Then** verify the correct labeled DOM cells, five typed value edges, four filter influences, exact dataset/operation bindings and required HTTP/JDBC/mapping evidence. Preserve capture limitations. Owner 2 guards against hardcoding the owner-1 response. A first-name-only template mutation must fail the name assertions even if its stale declaration still matches gold.

This behavior was exercised in the published prototype. Its nine offline negative controls include missing boundary/mapping evidence, an omitted last-name edge, ID incorrectly labeled as a value source, wrong transformation and incorrect dataset identities. The prototype reported full agreement for the selected edges, while exercising only 1/7 OwnerController routes and 1/20 of its Java branches. End-to-end capture completeness was not established. [Recorded comparison](https://github.com/Kart-rc/lineage-collection/blob/dba9967e17caf6343cddd5bee48614c0931a861f/docs/architecture-review/2026-10-06/petclinic-experiment/evidence/comparison.json), [coverage denominators](https://github.com/Kart-rc/lineage-collection/blob/dba9967e17caf6343cddd5bee48614c0931a861f/docs/architecture-review/2026-10-06/petclinic-experiment/evidence/coverage-denominators.json).

### Proposed reconciler acceptance matrix

| Scenario or mutation | Required assertion and reason |
|---|---|
| Same endpoints and context, valid declaration | Support only DECLARED_AND_EXECUTED; execution does not inspect semantics |
| Same table/field, different service endpoint | No cross-corroboration; catch Java-style overmatching |
| Runtime-only A→C | Candidate survives ingestion through query output; catch silent discovery loss |
| Static A→B absent from one run | Definition retained with bounded non-observation; avoid false retraction |
| SUM→MAX with same inputs/output and applicable path | Semantic conflict or failed independent oracle; topology alone must not pass |
| SUM and MAX on mutually exclusive branches | Keep both potential definitions; corroborate only the applicable path; unknown applicability stays unknown |
| VALUE→FILTER substitution in the expected mapping | Typed comparison fails; legitimate coexistence of both roles is allowed |
| Same parser produces two agreeing claims | Shared ancestry retained; no independent-witness promotion |
| New template, unchanged JAR/annotation | New context and stale-contract warning; catch partial artifact identity |
| Schema S1 evidence applied to S2 | No unproven carryover; historical evidence stays version-scoped |
| Drop required sensor, preserve COMPLETE | Capture gate fails independently of terminal status |
| Duplicate/reordered/late delivery | Same semantic result for the same final fact set |
| Same event ID, changed payload | Quarantine collision; no overwrite or confidence increase |
| Scoped retraction followed by replay | Target stays inactive; unaffected support and history remain |
| Partial replacement scan | No definition deletion without authoritative scope completion |
| Commit fails after successful mapper span | Attempt retained; no committed-output claim |

For the SUM mutation, choose inputs such as 1,250 and 750 for one group: SUM is 2,000 and MAX is 1,250. A one-row fixture would not distinguish them. Include a filtered-out row to test FILTER separately. Output checks, graph checks and mutation detection complement each other; none alone establishes complete semantic correctness.

Report at least six denominators: scenarios executed, potential operations exercised, instrumentable branches covered, typed-edge precision/recall against gold, required boundary associations and capture completeness. Empty denominators are N/A. Preserve unsupported constructs and unresolved cases. Do not report “100% lineage” because every observed edge matched a small static prediction set.

## 13 The smallest useful implementation sequence

1. **Preserve facts at the promotion boundary.** Retain runtime-only candidates, nonmatches and explicit reasons through normal ingestion, reconciliation and queries.
2. **Make matching context-aware.** Add operation, artifact-set, schema and configuration binding; prevent Java endpoint overmatching and historical support carryover.
3. **Separate semantic decisions from mechanism counts.** Include runtime counterevidence; preserve source classes and shared derivation; expose unknown equivalence.
4. **Add the versioned lifecycle.** Implement scoped replacement manifests, late evidence, retractions, deterministic replay and atomic serving revisions.
5. **Prove the normal product path.** Run independent scenarios through real capture, durable ingest, reconciliation and user-visible retrieval, including candidate/conflict history.

The first milestone should be a narrow chain with discriminating fixtures and measured gaps. It should not wait for a universal field-lineage sensor. Useful reconciliation is possible when limitations are explicit and every conclusion can be traced to scoped evidence.

## 14 Glossary

- **Static analysis:** interpreting program definitions without relying on a particular execution.
- **Runtime observation:** evidence captured while something executes; its granularity depends on the sensor.
- **Definition lineage:** potential dependencies for a particular versioned definition and context.
- **Execution lineage:** dependencies supported for a named run/attempt and its outcomes.
- **Canonical identity:** a resolved asset identity that avoids ambiguous aliases and cross-system collisions.
- **Value dependency:** an input contributes to the output value or transformation.
- **Control influence:** an input affects selection, grouping, joining, ordering or branching without being the copied value.
- **Corroboration:** compatible evidence supports the same scoped claim at its stated strength.
- **Counterevidence:** applicable evidence that challenges a claim or its validation.
- **Gold:** independently authored expected facts used to evaluate predictions within a declared scope.
- **Supersession:** a newer applicable claim replaces an earlier active interpretation, preserving history.
- **Retraction:** an explicit scoped withdrawal of an assertion's active support.
- **Replay:** re-deriving claims/decisions from retained evidence with pinned interpretation rules.
- **Bitemporal history:** tracking both when something applied and when the system learned it.

**Bottom line:** the reconciler should be able to answer, “Which exact fact supports this relation, for which deployed definition and run, what did the sensor actually establish, and what could change the conclusion?” If it cannot answer those questions, a stronger confidence label does not fix the underlying uncertainty.
