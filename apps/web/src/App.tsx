import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/layout/AppShell";
import { OperationsPage } from "./pages/OperationsPage";
import { LineageExplorerPage } from "./pages/LineageExplorerPage";
import { ProposalDetailPage } from "./pages/ProposalDetailPage";
import { ReviewQueuePage } from "./pages/ReviewQueuePage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";


export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<OperationsPage />} />
        <Route path="/review" element={<ReviewQueuePage />} />
        <Route path="/review/:proposalId" element={<ProposalDetailPage />} />
        <Route path="/lineage" element={<LineageExplorerPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
