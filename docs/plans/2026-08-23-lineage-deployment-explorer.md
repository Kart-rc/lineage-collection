# Lineage Deployment Explorer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and verify an offline interactive HTML explainer for the repository's SCA, runtime, API/UI, and AWS deployment flows.

**Architecture:** Add one hand-authored, dependency-free HTML/CSS/JavaScript page beside the generated canonical architecture rendering. Drive its journeys and component inventory from embedded immutable data, preserve the repository's evidence-status language, and validate both the static contract and real browser interactions without changing the canonical Mermaid renderer.

**Tech Stack:** Semantic HTML5, CSS custom properties and container/media queries, vanilla JavaScript, IBM Plex offline fonts, pytest documentation assertions, browser automation, Git.

---

### Task 1: Define the documentation contract

**Files:**
- Modify: `tests/test_documentation.py`
- Test: `tests/test_documentation.py`

**Step 1: Write the failing artifact and content test**

Add a constant and a focused test that reads the future HTML artifact and requires the approved
journeys, evidence boundaries, deployment services, all ten Lambda target names, source links, and
accessibility hooks:

```python
LINEAGE_DEPLOYMENT_EXPLORER = "docs/architecture/lineage-deployment-explorer.html"


def test_lineage_deployment_explorer_covers_collection_deployment_and_product_flows() -> None:
    explorer = _read(LINEAGE_DEPLOYMENT_EXPLORER)
    lowered = explorer.lower()

    for journey in ("sca collection", "runtime corroboration", "api and ui", "deployment"):
        assert journey in lowered
    for service in (
        "ecs fargate", "lambda", "sns", "sqs", "kinesis", "eventbridge",
        "api gateway", "cloudfront", "dynamodb", "s3", "ecr", "neptune",
    ):
        assert service in lowered
    for target in (
        "intake", "control-stage", "classification", "coverage",
        "runtime-validation", "consolidation", "proposal", "publication",
        "deployment", "product-api",
    ):
        assert f'data-component="{target}"' in explorer
    for status in ("verified", "synthesized", "partial", "planned", "AWS_REQUIRED", "NOT_CONFIGURED"):
        assert status in explorer
    for hook in ('aria-live="polite"', 'role="dialog"', 'aria-modal="true"', 'prefers-reduced-motion'):
        assert hook in explorer
    for source in (
        "infra/assets/lambda/Dockerfile", "infra/assets/sca/Dockerfile",
        "infra/lib/runtime-assets.ts", "infra/lib/engines-stack.ts",
        "infra/lib/intake-stack.ts", "scripts/deploy_ephemeral_aws.sh",
        "apps/api/src/lineage_api/application/workflows/definitions.py",
        "docs/architecture/lineage-platform-target.md",
    ):
        assert source in explorer
```

**Step 2: Run the focused test to verify it fails**

Run:

```bash
uv run --project apps/api --extra dev pytest -q tests/test_documentation.py -k lineage_deployment_explorer
```

Expected: FAIL because `docs/architecture/lineage-deployment-explorer.html` does not exist.

**Step 3: Commit the failing contract**

```bash
git add tests/test_documentation.py
git commit -m "test: define lineage deployment explorer contract"
```

### Task 2: Build the semantic offline explainer

**Files:**
- Create: `docs/architecture/lineage-deployment-explorer.html`
- Test: `tests/test_documentation.py`

**Step 1: Add the semantic page structure**

Create a complete HTML document with:

- an offline `../architecture-deck/fonts.css` import and no HTTP dependencies;
- skip link, sticky navigation, hero evidence boundary, and status legend;
- journey tabs and a stage containing graph, narrative, playback controls, and local/AWS adapter
  toggle;
- deployment artifact rail;
- component filters, result count, and inventory cards;
- known-gaps and source-trail sections;
- an initially closed modal detail drawer and polite live region;
- useful static fallback copy that explains every journey before JavaScript runs.

The embedded component data must identify exact responsibility, deployment form, inputs, outputs,
status, and repository sources. The deployment inventory must use the implementation's ten Lambda
targets rather than the stale README count.

**Step 2: Add the evidence-grounded journey data**

Define immutable JavaScript data for:

- `sca`: UI/API or signed event → durable command → EventBridge/SQS → Step Functions →
  classification/coverage → ARM64 Fargate SCA → S3 evidence → consolidation → proposal/review →
  fenced publication/Neptune;
- `runtime`: non-production session → OTel/OpenLineage/custom SDK → Kinesis → runtime-validation
  Lambda → S3 evidence → non-blocking consolidation;
- `product`: browser → CloudFront/S3 → API Gateway → product-api Lambda → DynamoDB/Neptune, plus
  collection status polling and the AWS submit `NOT_CONFIGURED` branch;
- `deployment`: clean revision → tests/build → wheel/web/OCI artifacts → digest manifests → base
  CDK stacks → ECR digest push → all stacks → CodeDeploy Lambda canaries/Fargate task → smoke →
  separately acknowledged cleanup.

**Step 3: Run the focused documentation test**

Run:

```bash
uv run --project apps/api --extra dev pytest -q tests/test_documentation.py -k lineage_deployment_explorer
```

Expected: PASS.

**Step 4: Validate the HTML and inline JavaScript syntax**

Run an `html.parser.HTMLParser` close-tag check and extract each non-JSON inline script into
`node:vm.Script`.

Expected: both commands exit 0 with no parse or syntax errors.

**Step 5: Commit the semantic artifact**

```bash
git add docs/architecture/lineage-deployment-explorer.html
git commit -m "docs: add lineage deployment explorer"
```

### Task 3: Implement journey playback and component exploration

**Files:**
- Modify: `docs/architecture/lineage-deployment-explorer.html`
- Modify: `tests/test_documentation.py`

**Step 1: Extend the static test with interaction hooks**

Require stable selectors for journey tabs, previous/next/play/reset buttons, adapter toggle, graph
nodes and edges, category/status filters, result count, modal close button, and hash-state parsing.

**Step 2: Run the focused test and verify the new assertions fail**

Run the Task 1 focused pytest command.

Expected: FAIL on at least one missing interaction hook.

**Step 3: Implement a single deterministic state store**

Add plain JavaScript that owns:

```javascript
const state = {
  journey: "sca",
  step: 0,
  playing: false,
  adapter: "aws",
  statuses: new Set(["verified", "synthesized", "partial", "planned"]),
  category: "all",
  selectedComponent: null,
};
```

Render journey nodes/edges, narrative, progress, inventory visibility, and result counts only from
this state. Ensure repeated render calls are idempotent and animation timers are cancelled on
journey changes, reset, reduced-motion preference, and page unload.

**Step 4: Implement accessible controls and hash restoration**

- tabs follow the ARIA tab pattern and support arrow keys;
- playback buttons update labels and disabled states;
- component buttons open the dialog, focus the close button, trap Tab within the dialog, restore
  the opener on close, and close on Escape/backdrop;
- filters update `aria-pressed` and the polite result announcement;
- `#journey=<id>&component=<id>` restores a shareable selection without unsafe HTML insertion.

**Step 5: Run focused tests and syntax validation**

Expected: documentation assertions and JavaScript parsing both PASS.

**Step 6: Commit the interactions**

```bash
git add docs/architecture/lineage-deployment-explorer.html tests/test_documentation.py
git commit -m "docs: make lineage architecture flows interactive"
```

### Task 4: Refine the visual system and responsive behavior

**Files:**
- Modify: `docs/architecture/lineage-deployment-explorer.html`

**Step 1: Implement the approved flight-deck visual system**

Use CSS variables for ink, blueprint panels, cyan signals, green verified state, amber warnings, and
muted planned state. Pair IBM Plex Sans headings/body with IBM Plex Mono artifacts and identifiers.
Add restrained grid/noise atmosphere, strong focus rings, labeled connection lines, compact status
chips, and a prominent warning rail for `AWS_REQUIRED`/`NOT_CONFIGURED`.

**Step 2: Add responsive layouts**

- desktop: graph and narrative split view, sticky controls, multi-column component inventory;
- tablet: stacked journey content and two-column inventory;
- mobile: single-column layout, horizontally scrollable tab list, touch-sized controls, graph nodes
  as an ordered vertical route, and full-screen detail drawer.

**Step 3: Add motion and reduced-motion behavior**

Animate only the active path and the initial section reveal. Under `prefers-reduced-motion: reduce`,
remove transitions, stop autoplay, and show the selected state immediately.

**Step 4: Re-run static verification**

Expected: focused test, HTML parser, and JavaScript syntax checks PASS.

**Step 5: Commit the visual refinement**

```bash
git add docs/architecture/lineage-deployment-explorer.html
git commit -m "style: refine lineage architecture flight deck"
```

### Task 5: Reconcile the README and verify source accuracy

**Files:**
- Modify: `README.md:5`
- Modify: `tests/test_documentation.py`

**Step 1: Add a failing count-reconciliation assertion**

Require the README to say `ten independently addressable Lambda handlers` and the HTML to expose
ten unique `data-component` Lambda target cards.

**Step 2: Run the focused test to verify it fails**

Expected: FAIL because README still says nine.

**Step 3: Correct the README count**

Change only the stale word `nine` to `ten`; do not alter the generated canonical architecture HTML.

**Step 4: Run documentation and architecture checks**

```bash
uv run --project apps/api --extra dev pytest -q tests/test_documentation.py
make architecture-check
make workflow-check
```

Expected: PASS for all commands.

**Step 5: Commit the reconciliation**

```bash
git add README.md tests/test_documentation.py
git commit -m "docs: reconcile Lambda deployment inventory"
```

### Task 6: Run real-browser interaction and visual verification

**Files:**
- Verify: `docs/architecture/lineage-deployment-explorer.html`

**Step 1: Start a bounded local static server**

Serve the repository root on `127.0.0.1` using an available high port and retain its process ID for
cleanup.

**Step 2: Run the desktop interaction smoke**

At approximately 1440×1000:

- select all four journeys and verify title/narrative/path changes;
- use next, previous, play/pause, and reset;
- switch local/AWS adapters;
- filter by status and category and verify the result count;
- open a Lambda component, verify exact source links, close with Escape, and confirm focus returns;
- reload a journey/component hash and verify restoration;
- require zero console errors and zero failed network requests.

**Step 3: Inspect a desktop screenshot**

Verify hierarchy, line labels, status contrast, no overlap/clipping, and readable artifact/source
text. Iterate on the HTML until the screenshot is visually clean.

**Step 4: Run the mobile smoke and inspect a screenshot**

At approximately 390×844, verify single-column flow, scrollable tabs, touch targets, full-screen
drawer, readable cards, and no horizontal page overflow. Iterate until clean.

**Step 5: Verify reduced motion and static fallback**

Emulate reduced motion and confirm autoplay is unavailable/stopped. Disable JavaScript and confirm
the journeys, artifacts, statuses, and source trail remain understandable.

**Step 6: Stop the static server**

Terminate only the retained server process and confirm the port is closed.

### Task 7: Completion audit and final verification

**Files:**
- Verify: `README.md`
- Verify: `docs/architecture/lineage-deployment-explorer.html`
- Verify: `docs/plans/2026-08-23-lineage-deployment-explorer-design.md`
- Verify: `tests/test_documentation.py`

**Step 1: Run the complete relevant verification set**

```bash
uv run --project apps/api --extra dev pytest -q tests/test_documentation.py
make architecture-check
make workflow-check
git diff --check main...HEAD
```

Expected: all commands PASS.

**Step 2: Audit every explicit objective item**

Confirm the HTML visibly and interactively covers:

- SCA plus runtime lineage collection and their consolidation semantics;
- current project components and intended usage;
- Dockerfiles and every deployment artifact;
- ECS Fargate, all Lambda targets, SNS, SQS, Kinesis, Step Functions, API Gateway, and CloudFront;
- UI submission/status and query/review flows;
- deployment, canary, smoke, rollback/cleanup, and live-AWS evidence boundaries;
- authoritative source links and honest current gaps.

**Step 3: Review the final diff and worktree scope**

Confirm only the planned files changed and all pre-existing untracked user files remain untouched.

**Step 4: Commit any final scoped corrections**

```bash
git add README.md docs/architecture/lineage-deployment-explorer.html tests/test_documentation.py
git commit -m "docs: finalize lineage deployment explorer"
```

Skip this commit when the index is empty.
