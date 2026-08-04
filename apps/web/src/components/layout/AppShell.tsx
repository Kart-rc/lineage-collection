import { useQuery } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";

import { api } from "../../api/client";
import { NavLink } from "../../routing";
import { StatusPill } from "../shared/StatusPill";


const NAVIGATION = [
  { to: "/", label: "Operations", end: true },
  { to: "/review", label: "Review queue" },
  { to: "/lineage", label: "Lineage explorer" },
  { to: "/runs", label: "Runs" },
];


export function AppShell({ children }: PropsWithChildren) {
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => api.overview(signal),
    staleTime: 5_000,
  });

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="topbar">
        <NavLink className="wordmark" to="/" aria-label="Lineage control room home">
          <span className="wordmark__sigil" aria-hidden="true">
            L/
          </span>
          <span>
            <strong>Lineage</strong>
            <small>control room</small>
          </span>
        </NavLink>
        <div className="topbar__context" aria-live="polite">
          <StatusPill label="Seeded prototype" tone="neutral" />
          <span className="namespace-watermark">
            {overview.isPending
              ? "Resolving active graph…"
              : overview.isError
                ? "Active graph unavailable"
                : `Active graph ${overview.data.activeVersion}`}
          </span>
        </div>
      </header>
      <nav className="primary-nav" aria-label="Primary">
        {NAVIGATION.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => (isActive ? "is-active" : undefined)}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <main id="main-content" tabIndex={-1}>
        {children}
      </main>
      <footer className="app-footer">
        <span>Evidence before assertion.</span>
        <span>Local M1 walking skeleton · 2026-08-04</span>
      </footer>
    </div>
  );
}
