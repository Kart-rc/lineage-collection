import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "./routing";

import { App } from "./App";


beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        activeVersion: "v1",
        fencingToken: 0,
        counts: { runs: 0, inReview: 0, quarantined: 0 },
        recentRuns: [],
      }),
    }),
  );
});


afterEach(() => vi.unstubAllGlobals());


test("provides an accessible control-room shell and namespace watermark", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute(
    "href",
    "#main-content",
  );
  const navigation = screen.getByRole("navigation", { name: "Primary" });
  for (const name of [
    "Operations",
    "Onboarding",
    "Review queue",
    "Lineage explorer",
    "Interactions",
    "Runs",
  ]) {
    expect(navigation).toHaveTextContent(name);
  }
  expect(await screen.findByText(/Active graph v1/)).toBeVisible();
  expect(screen.getByText("Seeded prototype")).toHaveTextContent("Seeded prototype");
});
