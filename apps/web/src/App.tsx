import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/layout/AppShell";


function Placeholder({ eyebrow, title, copy }: { eyebrow: string; title: string; copy: string }) {
  return (
    <section className="page page--placeholder">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      <p className="lede">{copy}</p>
    </section>
  );
}


export function App() {
  return (
    <AppShell>
      <Routes>
        <Route
          path="/"
          element={
            <Placeholder
              eyebrow="System posture"
              title="Operations"
              copy="Follow one signed delivery from intake to an accepted graph projection."
            />
          }
        />
        <Route
          path="/review"
          element={
            <Placeholder
              eyebrow="Human gate"
              title="Review queue"
              copy="Inspect confidence, corroboration, and immutable source evidence."
            />
          }
        />
        <Route
          path="/lineage"
          element={
            <Placeholder
              eyebrow="Active projection"
              title="Lineage explorer"
              copy="Traverse version-pinned relationships and simulate downstream impact."
            />
          }
        />
        <Route
          path="/runs"
          element={
            <Placeholder
              eyebrow="Execution ledger"
              title="Runs"
              copy="Read every durable stage transition with its shared correlation identity."
            />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
