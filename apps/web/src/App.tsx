import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/layout/AppShell";
import { OperationsPage } from "./pages/OperationsPage";
import { ProposalDetailPage } from "./pages/ProposalDetailPage";
import { ReviewQueuePage } from "./pages/ReviewQueuePage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";


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
        <Route path="/" element={<OperationsPage />} />
        <Route path="/review" element={<ReviewQueuePage />} />
        <Route path="/review/:proposalId" element={<ProposalDetailPage />} />
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
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
