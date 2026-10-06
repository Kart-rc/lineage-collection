# Interactive lineage guide: validation report

## Artifact and scope

- Artifact: `lineage-interactive-guide.html`, 96,076 bytes, standalone HTML/CSS/JavaScript.
- SHA-256: `778127a5a771616637471cf632085aa2f6d0142f13fa5b8a746ae6a0eb495068`.
- Repository evidence baseline: `32958a35cdf49341b15a4d6cb05f596811396b06`.
- No CDN, backend, analytics, external script, model request, application account or saved browser state is required. The source-reference hyperlinks are optional external navigation.
- All example business values, quality counts, capacity inputs and ATDD coverage counters are explicitly synthetic/illustrative; they are not measured product results.

## Executed regression validation

**689 assertions passed, zero JavaScript/CSS initialization or interaction errors.** The full embedded JavaScript was executed in JSDOM 26.1.0 with the actual generated HTML. Tests dispatched DOM click/input/change events; the application functions were not replaced with stub implementations. This is DOM/JavaScript execution, not a real browser rendering test.

Checks include:

- Eight main sections, unique IDs, valid internal anchors, accessible input labels and safe external link relationships.
- No remote resource references, network/storage API calls, forms or embedded frames; restrictive network CSP.
- All 18 architecture nodes across three repeated cycles (54 node selections).
- Every field-trace step, both value/control views, forward/back boundaries and reset.
- All 32 combinations of five evidence filters, including empty state and six distinct evidence cards.
- Five failure scenarios and their ten answer/test disclosure panels.
- Precision, recall and capture formulas; presets; zero-prediction precision correctly shown as undefined.
- Capacity defaults, burst/backfill arithmetic, empty/negative/maximum inputs, unstable queues, and zero-queue equal-arrival/capacity boundary.
- Four instrumentation choices × 32 ATDD scenario combinations, six distinct denominators, no-scenario N/A and explicit capture loss.
- Twelve challenge answers, reveal/hide all, six rehearsal checks and global reset.
- No private filesystem paths, private schedule context or user identifiers in the HTML. Repository links use the pinned commit; linked source paths exist in the reviewed checkout.

The instrumentation hierarchy is explicit: service OTel auto-instrumentation → customized OTel field evidence where feasible → separate SDK only where OTel customization is infeasible; Spark-native plans; all lineage normalized into OpenLineage through a semantic adapter, with standard facets and versioned namespaced extensions.

## Reproduce

From the repository root with its existing development dependencies installed:

```sh
node docs/architecture-review/2026-10-06/lineage-interactive-guide.qa.cjs \
  docs/architecture-review/2026-10-06/lineage-interactive-guide.html
```

The script uses the repository's existing JSDOM dependency. It performs no network requests. The adjacent JSON results record the assertion count and limitations.

## Visual/browser validation: not established

- The cloud browser rejected the local file protocol under its URL policy; the standard local HTTP preview returned `ERR_BLOCKED_BY_CLIENT`.
- An ordinary installed Chromium headless renderer failed before page load with a process-singleton socket permission failure (exit 134). No security settings were changed to bypass this.
- Consequently, desktop/mobile screenshots, real-browser keyboard behavior, pixel layout, clipping/overflow, print layout and assistive-technology operation were **not visually verified**.
- Responsive breakpoints, reduced-motion rules, native buttons/inputs/disclosures, visible focus and semantic labels are implemented and structurally inspected. That is not a substitute for rendered verification and is not represented as one.
- External reference links were inherited from the primary-source research and checked structurally; this QA pass did not re-fetch every external page.

## Suggested remaining manual acceptance

Open the downloaded file in a supported modern browser, test desktop and narrow mobile widths, inspect every section for horizontal overflow, tab through controls, and exercise Reset. Confirm the scenario calculators are read as educational examples. No deployment or external service is required.
