import { useQuery } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";

import { api } from "../../api/client";
import { readRuntimeConfig } from "../../config/runtime";
import { NavLink } from "../../routing";
import { StatusPill } from "../shared/StatusPill";


const NAVIGATION = [
  { to: "/", label: "Operations", end: true, lens: "operations" },
  { to: "/onboard", label: "Onboarding", lens: "onboarding" },
  { to: "/review", label: "Review queue", lens: "review" },
  { to: "/lineage", label: "Lineage explorer", lens: "lineage" },
  { to: "/interactions", label: "Interactions", lens: "interactions" },
  { to: "/runs", label: "Runs", lens: "runs" },
];


export function AppShell({ children }: PropsWithChildren) {
  const runtime = readRuntimeConfig();
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => api.overview(signal),
    staleTime: 5_000,
  });

  const tenant = overview.data?.recentRuns[0]?.system;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="topbar">
        <NavLink className="wordmark" to="/" aria-label="Lineage control room home">
          <span className="wordmark__sigil" aria-hidden="true" />
          <span>
            <strong>Throughline</strong>
            <small>Lineage control room</small>
          </span>
        </NavLink>
        <span className="topbar__divider" aria-hidden="true" />
        <span className="topbar__tenant">
          {tenant ? `${tenant} · ${runtime.environment}` : runtime.environment}
        </span>
        <nav className="primary-nav" aria-label="Primary">
          {NAVIGATION.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              data-lens={item.lens}
              className={({ isActive }) => (isActive ? "is-active" : undefined)}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="topbar__context" aria-live="polite">
          <StatusPill
            label={runtime.demoActions ? "Seeded prototype" : runtime.environment}
            tone="neutral"
          />
          <span className="namespace-watermark">
            {overview.isPending
              ? "Resolving active graph…"
              : overview.isError
                ? "Active graph unavailable"
                : overview.data.activeVersion
                  ? `Active graph ${overview.data.activeVersion} · fence ${overview.data.fencingToken}`
                  : "No active graph"}
          </span>
          <span className="topbar__avatar" aria-hidden="true" />
        </div>
      </header>
      <main id="main-content" tabIndex={-1}>
        {children}
      </main>
      <footer className="app-footer">
        <span>Evidence before assertion — no edge publishes without its receipt.</span>
        <span>
          {runtime.environment} · revision {runtime.sourceRevision}
        </span>
      </footer>
    </div>
  );
}
