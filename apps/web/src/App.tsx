import { AppShell } from "./components/layout/AppShell";
import { OperationsPage } from "./pages/OperationsPage";
import { LineageExplorerPage } from "./pages/LineageExplorerPage";
import { ProposalDetailPage } from "./pages/ProposalDetailPage";
import { ReviewQueuePage } from "./pages/ReviewQueuePage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";
import { Navigate, usePathname } from "./routing";


export function App() {
  const pathname = usePathname();
  let page;

  if (pathname === "/") page = <OperationsPage />;
  else if (pathname === "/review") page = <ReviewQueuePage />;
  else if (/^\/review\/[^/]+$/.test(pathname)) page = <ProposalDetailPage />;
  else if (pathname === "/lineage") page = <LineageExplorerPage />;
  else if (pathname === "/runs") page = <RunsPage />;
  else if (/^\/runs\/[^/]+$/.test(pathname)) page = <RunDetailPage />;
  else page = <Navigate to="/" replace />;

  return (
    <AppShell>
      {page}
    </AppShell>
  );
}
