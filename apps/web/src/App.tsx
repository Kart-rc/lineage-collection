import { AppShell } from "./components/layout/AppShell";
import { InteractionsPage } from "./pages/InteractionsPage";
import { OnboardingPage } from "./pages/OnboardingPage";
import { OperationsPage } from "./pages/OperationsPage";
import { LineageExplorerPage } from "./pages/LineageExplorerPage";
import { ProposalDetailPage } from "./pages/ProposalDetailPage";
import { ReviewQueuePage } from "./pages/ReviewQueuePage";
import { RunComparePage } from "./pages/RunComparePage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";
import { Navigate, usePathname } from "./routing";


export function App() {
  const pathname = usePathname();
  let page;

  if (pathname === "/") page = <OperationsPage />;
  else if (pathname === "/onboard") page = <OnboardingPage />;
  else if (pathname === "/review") page = <ReviewQueuePage />;
  else if (/^\/review\/[^/]+$/.test(pathname)) page = <ProposalDetailPage />;
  else if (pathname === "/lineage") page = <LineageExplorerPage />;
  else if (pathname === "/interactions") page = <InteractionsPage />;
  else if (pathname === "/runs") page = <RunsPage />;
  else if (pathname === "/runs/compare") page = <RunComparePage />;
  else if (/^\/runs\/[^/]+$/.test(pathname)) page = <RunDetailPage />;
  else page = <Navigate to="/" replace />;

  return (
    <AppShell>
      {page}
    </AppShell>
  );
}
