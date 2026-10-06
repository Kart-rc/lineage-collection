# Independent gold: Spring Petclinic owner details

## Frozen source and scope

Repository: `spring-projects/spring-petclinic` at commit
`500158f732419217507c7656904b8e6aa1bcc0d6`.

Primary execution: a real HTTP `GET /owners/1` against the actual application,
fresh H2 schema/data and a new English-default session. Score only the four
owner-summary table cells, not pets, visits, links, layout or all HTML.

The independent author inspected the pinned official controller, repository,
entity mappings, template, schema, seed data and configuration. Ten local source
files were checked against GitHub blob IDs at this exact commit; see
`pinned_source_manifest.json`. No instrumentation, adapter, trace or prediction
was inspected. `owner_details_gold.json` is the oracle; its generator contains
manually adjudicated facts, not a pipeline which derives gold from predictions.

## Expected graph

Five value edges into four rendered targets:

- `owners.first_name` -> `owner.name`: concatenation with last name and a space,
  then escaped Thymeleaf text rendering
- `owners.last_name` -> `owner.name`: the same compound transformation
- `owners.address` -> `owner.address`: escaped HTML text rendering
- `owners.city` -> `owner.city`: escaped HTML text rendering
- `owners.telephone` -> `owner.telephone`: escaped HTML text rendering

Also record `owners.id` -> each of those four targets as FILTER influence from
`findById(ownerId)`. The request path supplies the lookup parameter. ID is not a
VALUE contributor to these four cells. It does contribute to edit/add links,
which are explicitly out of scope.

The Name cell is a single sink; the page does not separately render first and
last name. Hydration and getter hops may preserve values, but the complete edge
to rendered HTML is not IDENTITY. For this contract, `HTML_RENDER` includes
escaping and serialization. Parsed DOM text matching the source is compatible
with this transformation; it does not justify erasing it. The official
[Thymeleaf documentation](https://www.thymeleaf.org/doc/tutorials/3.1/usingthymeleaf.html#unescaped-text)
confirms that `th:text` escapes markup.

## Fixtures and HTTP evidence

Owner 1: Name `George Franklin`; Address `110 W. Liberty St.`; City `Madison`;
Telephone `6085551023`.

Control owner 2: Name `Betty Davis`; Address `638 Cardinal Ave.`; City
`Sun Prairie`; Telephone `6085551749`.

These are from H2 `data.sql` lines 25–26 and the fresh identity-column schema.
Require 200, no redirect, HTML content type, and exactly the expected values
inside the four correctly labeled owner-summary rows. Keep raw response bytes
and parsed values. Use actual HTTP; a mocked repository MVC test does not
establish database-to-response behavior. Global substring checks are too weak.
Owner 2 is important: a hardcoded owner-1 implementation can pass owner 1 alone.

Do not assert exactly one repository invocation: both `findOwner` (the model
attribute initializer) and `showOwner` call `findById`. Deduplicate graph edges,
while retaining the number of raw observations as separate evidence.

## Evaluation and mutation checks

Score source-target topology, transformation labels, filter influences and
public output assertions separately. A transform-label defect can pass topology
metrics; a correct HTML response can coexist with an incomplete lineage graph.
Freeze gold before prediction inspection and report its checksum. Alias mapping
is permitted only as explicit schema normalization, without changing facts.

Mandatory proposed negative controls are omission of the last-name edge,
addition of ID as a value source, and relabeling render transformations as
IDENTITY. Public-output mutations are swapped address/city expressions,
first-name-only display and a hardcoded lookup ID. The benign seed values cannot
exercise escaping: a separately declared extension fixture with address
`A & <em>Probe</em>` distinguishes `th:text` from `th:utext` by checking text and
absence of an injected `em` child. Do not claim that extension was executed
unless it actually was.

## GET versus owner-creation POST

Use GET as the primary slice. It keeps the oracle bounded and independently
checkable against checked-in seed data. A POST to `/owners/new`, followed by its
redirected GET, would add binding, validation, generated IDs, a write,
redirect/flash state and cross-request correlation. That is a useful second
experiment, but not a necessary substitute for this read-path check. Creation
would require a separate gold contract and an isolated disposable database.

## Status

Oracle and mutation design authored and source hashes validated. This author
has not run the Petclinic server, real HTTP assertions, mutations or lineage
instrumentation. Runtime results must be supplied by the execution harness.
