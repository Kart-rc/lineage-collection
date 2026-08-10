# Real Java Spring Repository Lineage Design

**Date:** 2026-08-09

**Status:** Approved for implementation

**Goal:** Prove the static lineage path against an exact checkout of the canonical Spring Petclinic
repository instead of treating the Python demo fixture as evidence of Java/Spring compatibility.

## 1. Evidence that motivates the change

The canonical `spring-projects/spring-petclinic` repository was cloned and pinned at revision
`88e37c15cf6fc8490b01bc3e8e2c800cec1ac272`. It contains Spring Boot Maven and Gradle builds,
Spring Data JPA repositories, JPA entity/table annotations, H2/MySQL/PostgreSQL schemas and
datasource profiles.

The unchanged collector was run against every tracked `src/main/java/**/*.java` path. It returned
`filesAnalyzed=0`, `edgesEmitted=0`, `residueCount=0` and `quarantinedCount=0`. This is an
unsupported-analyzer result, not a pass: the local orchestrator is fixture-root bound and the
existing analyzer only parses a Python demo convention.

## 2. Alternatives

### A. Regular-expression Java scanning

This is dependency-light but cannot reliably distinguish annotations, nested types, comments,
method invocations or malformed input. It would turn syntax ambiguity into false confidence and is
rejected.

### B. Execute Maven/Gradle and use a JVM semantic analyzer

JavaParser, OpenRewrite or compiler attribution can eventually provide deeper type resolution, but
executing arbitrary repository builds requires a Java toolchain, dependency-network access and a
stronger sandbox. It also lets build plugins and annotation processors run. This is deferred to an
explicitly sandboxed advanced pack rather than made a prerequisite for the first real-repository
proof.

### C. Source-only Tree-sitter Java pack with SQL schema corroboration — selected

Use the maintained Tree-sitter Java grammar for a real syntax tree without executing repository
code. Use SQLGlot with an explicit dialect to parse checked-in schemas. Resolve only local,
evidence-backed Spring Data/JPA relationships. Unsupported or ambiguous constructs become bounded
residue. This proves useful Spring lineage safely and creates a clean seam for a later JVM semantic
pack.

## 3. Trust boundary and source contract

Source acquisition is separate from analysis. For the local proof, an operator or CI job clones the
repository and passes an exact checkout descriptor to the collector. Production continues to pass a
versioned S3 source-archive reference to the SCA task; the analyzer never clones from the internet.

The local descriptor contains:

- canonical HTTPS repository URL;
- repository name;
- exact 40- or 64-character lowercase commit digest;
- checkout root;
- environment, platform and system;
- requested analyzer pack and ruleset version.

The local source adapter verifies the checkout is a Git work tree, `HEAD` equals the requested
digest, the configured origin matches after credential removal, and the index path/mode/OID mapping
equals the exact requested revision tree before and after blob acquisition. It analyzes only bounded
committed blob bytes addressed by that mapping; dirty or EOL-transformed regular worktree bytes are
not analyzed. Selected worktree paths must remain regular with the committed executable mode and
below the checkout root; symlinks, submodules and non-regular replacements fail closed.

Repository code is hostile input. The collector does not run Maven, Gradle, shell scripts,
annotation processors, tests or application code and does not expose credentials or unrestricted
network access.

## 4. Versioned analyzer pack

`java-spring-data-jpa-v1` is a closed, independently versioned pack. Pack selection is based on
checked-in build evidence, not file extension alone:

- `pom.xml` or `build.gradle` identifies a Java build;
- a Spring Boot plugin/parent and Spring Data JPA dependency identify the supported framework cell;
- unsupported build languages, framework versions or conflicting build facts are recorded rather
  than silently assigned to the pack.

The pack uses pinned `tree-sitter` and `tree-sitter-java` packages. Tree-sitter parse errors are
recorded per file with byte/line scope. The pack uses SQLGlot to parse the selected H2, MySQL or
PostgreSQL schema with the matching dialect; a table is trusted only when it exists in the parsed
schema for the selected profile.

## 5. Static facts and lineage semantics

The Java AST pass extracts only supported facts with exact locations:

1. JPA entity classes annotated with `@Entity` and their `@Table(name=...)` mapping. The default
   entity-name convention is allowed only when the schema contains exactly one matching table.
2. Spring Data repository interfaces extending `JpaRepository<Entity, Id>` or a configured
   compatible base interface.
3. Controller or service fields/constructor parameters whose declared type resolves to one local
   repository interface.
4. Method invocations on those bound repository variables.
5. Explicit `@Query` text when it is a compile-time string and SQL/JPQL parsing can identify the
   operation without guessing.

Supported operation mapping is conservative:

- `find*`, `get*`, `read*`, `count*`, `exists*` and parsed `SELECT` produce `READS`;
- `save*`, `insert*`, `update*` and parsed `INSERT`/`UPDATE` produce `WRITES`;
- `delete*`, `remove*` and parsed `DELETE` produce `WRITES` with a delete transform;
- unknown methods, dynamic query construction, unresolved repository types, missing tables and
  ambiguous profiles produce residue, not edges.

A read edge is `table -> service operation`; a write edge is `service operation -> table`. Service
operation references use a stable `service://<repo>/<fully-qualified-class>#<method>` identity.
Tables are normalized through the pinned catalog/resolver into environment/platform/system dataset
URNs. Static evidence records the invocation file/line, AST path, repository-interface/entity/table
facts, exact commit, ruleset and resolver versions. `exact=true` means the cited source mapping is
exact; it does not claim the operation executed at runtime.

Inherited CRUD capability without a cited call site is inventory evidence only and never becomes a
lineage edge. Static confidence stays `SINGLE` until independent runtime or another mechanism
corroborates the same normalized relationship.

## 6. Application flow

The CLI receives the checkout descriptor and routes it through existing application boundaries:

```text
verify checkout and determinant pins
  -> classify Java/Spring framework cell
  -> enumerate bounded tracked scope
  -> parse schemas and Java AST
  -> write immutable SCA evidence and coverage manifest
  -> consolidate supported edges
  -> create/reuse a review proposal
  -> optional explicit reviewer approval
  -> existing staged/verified/fenced publication
```

The default real-repository command stops at `IN_REVIEW`. It never auto-approves or auto-publishes.
The existing review and publication paths remain the only way to activate graph changes.

The Python demo analyzer remains available under its own explicit pack. Selection is closed: a Java
repository cannot fall through to the Python analyzer, and an unknown pack fails before analysis.

## 7. Failure and recovery behavior

- Revision, origin, index/tree, path type/mode, symlink or non-regular drift fails closed. Dirty
  regular worktree bytes are outside source evidence because exact committed blobs are authoritative.
- Unsupported framework cells return `INTEGRATION_REQUIRED` with coverage and residue; zero files
  analyzed cannot be reported as success.
- Parser failures are per-file residue when the rest of the bounded scope is safe to analyze. A
  resource-bound or source-integrity failure terminates the work unit.
- Resolver or schema ambiguity quarantines the affected relationship. No guessed table identity is
  emitted.
- The evidence identity includes repository URL, revision, scope digest, analyzer pack, ruleset,
  schema profile, catalog snapshot and resolver version. Retrying the same determinant set returns
  the same byte-identical evidence reference and proposal identity.
- Raw source and datasource credentials never enter workflow state or logs.

## 8. Verification and acceptance

Implementation follows RED -> GREEN -> REFACTOR with isolated commits and journal checkpoints.

1. Unit corpus: positive Spring Data calls, entity/table mapping, explicit queries, nested syntax,
   comments/strings, malformed Java, unknown methods, dynamic queries and hostile paths.
2. Determinism: reordered filesystem enumeration and replay produce byte-identical evidence.
3. Checkout security: wrong revision/origin, index/path/type/mode drift, symlinks, submodules, path
   escape, credential-bearing URLs and size/count bounds fail closed. Analysis reads bounded exact
   committed blobs; regular dirty or EOL-transformed worktree bytes are never analyzed.
4. Flow integration: a real-repository result reaches `IN_REVIEW`, preserves coverage and exact
   citations, and can use the existing explicit review/fenced publication path.
5. Real compatibility cell: an opt-in test runs against the pinned Spring Petclinic checkout and
   asserts nonzero Java files analyzed, the exact supported repository/entity/table set, nonzero
   read/write edges, zero silent skips and a content-addressed evidence manifest.
6. Regression gates: backend, web, infrastructure, workflow drift and local acceptance remain green.

The real-repository evidence records the external URL and commit but does not vendor Petclinic source
into this repository. A network-unavailable CI cell is `INTEGRATION_REQUIRED`, not `PASS`.

## 9. Deferred runtime slice

After this static cell is committed and independently verified, the next slice starts the same exact
Petclinic revision with an approved OTel Java agent/profile, exercises bounded HTTP/database
operations, ingests OTLP evidence and joins runtime `READS`/`WRITES` observations to the normalized
static relationships. Runtime absence does not lower the validity of static evidence, but static
evidence alone never claims execution.
