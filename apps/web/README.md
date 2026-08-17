# `@lineage-collector/web` — the control room

The operator interface. Nine screens covering the full loop: onboard a repository,
watch it move through the collection stages, review the evidence behind every
proposed edge, approve and publish, then explore the resulting graph and run impact
analysis against it.

React 18 + TypeScript + Vite, with TanStack Query for server state. No component
library and no CSS framework — the design system is hand-written and lives in
`src/styles/`.

---

## Screens

| Route | Screen | What it is for |
|---|---|---|
| `/` | Operations | Active pointer and fence, counts, subsystem health, recent runs, deployment control |
| `/onboard` | Onboarding | Three-step wizard that submits a repository for collection and polls its status |
| `/review` | Review queue | Proposals filtered by state — `IN_REVIEW`, `APPROVED`, `REJECTED`, `FINALIZED` |
| `/review/:id` | Proposal detail | The evidence view. Added, removed and band-changed edges, provenance, decision form |
| `/lineage` | Lineage explorer | Graph walk from a subject URN, with an edge inspector and impact panel |
| `/interactions` | Interactions | Service-to-service call plane, kept separate from dataset lineage |
| `/runs` | Runs | Paged run history with inline detail |
| `/runs/compare` | Run compare | Two runs side by side, including their proposal edge diff |
| `/runs/:id` | Run detail | Stage-by-stage timeline for a single run |

### The screen that matters most

`/review/:id` is where the product's argument is made. It splits proposed edges into
**runtime-verified** and **needs your verification — static-only**, and for each edge
shows its confidence band, the `file:line` citation behind the claim, the mechanisms
that asserted it, and the evidence object's checksum. A reviewer is never asked to
trust a number without being shown what produced it.

---

## Layout

```
src/
├── api/          typed client and response types — the single boundary to the backend
├── components/
│   ├── layout/       app shell and navigation
│   ├── lineage/      graph canvas, edge inspector, impact panel
│   ├── operations/   health tiles, deployment control, collection form
│   ├── review/       edge rows, provenance panel, confidence presentation
│   ├── runs/         stage timeline, status pills
│   └── shared/       cross-screen primitives
├── config/       runtime configuration parsing, with a strict field allowlist
├── hooks/        data-fetching hooks built on TanStack Query
├── pages/        one component per route
└── styles/       design tokens and per-page stylesheets
```

---

## Running it

### Prerequisites

Node.js 20 or newer. The API must be running for the UI to show anything — it holds
no fixtures of its own.

### Normal path

From the repository root:

```bash
make dev
```

That starts the API on `:8000` and this app on <http://127.0.0.1:5173>, which is the
combination the demo walkthrough assumes.

### This workspace alone

```bash
npm run dev --workspace apps/web
```

Vite proxies `/api` to `http://127.0.0.1:8000`, so the API still needs to be running
separately.

### Other commands

```bash
npm run build   --workspace apps/web   # strict tsc build, then a production bundle
npm test        --workspace apps/web   # Vitest + Testing Library
npm run preview --workspace apps/web   # serve the built bundle on :4173
```

---

## Runtime configuration

The app reads an optional `__LINEAGE_RUNTIME_CONFIG__` global injected by the host
page, falling back to a frozen local default. `src/config/runtime.ts` parses it
against a **strict field allowlist** — `schemaVersion`, `environment`, `apiBasePath`,
`sourceRevision`, `demoActions` — and rejects the whole object if it carries anything
else.

That allowlist is a security control, not tidiness: it is what stops a secret from
being smuggled into client-side config, and `src/config/runtime.test.ts` asserts
exactly that by attempting to pass a `secret` field and requiring rejection. Keep the
allowlist closed when adding fields.

`demoActions` gates the destructive demo controls, such as *Run seeded collection* and
state reset. It is on for `local` and off for `production`.

---

## Conventions worth keeping

- **All server access goes through `src/api/`.** No component calls `fetch` directly.
- **Confidence presentation is derived, never authored.** `components/review/reviewMeta.ts`
  mirrors the backend's band-to-label mapping. If the backend's bands change, change
  it here in one place.
- **Styling is plain CSS with tokens.** Per-page sheets live in `src/styles/pages/`.
- **Tests render real components** with Testing Library rather than asserting on
  implementation details.

---

## Known gap

The Lineage Explorer initialises its direction control to `both` and offers it as an
option, but the local FastAPI backend accepts only `up` or `down` and rejects anything
else with `INVALID_DIRECTION`. Only the AWS/Neptune adapter implements `both`. Against
`make dev`, the explorer therefore errors until the direction is changed. Fix it in
either `LineageExplorerPage.tsx` or `services/query.py`, depending on which behaviour
you want to be authoritative.

If `LINEAGE_API_TOKEN` is set on the API, this app will receive `401` — it does not
yet send a bearer token. Leave the token unset for the local UI walkthrough.

---

## Related

- [Repository overview and navigation](../../docs/NAVIGATION.md)
- [Backend API](../api/README.md)
