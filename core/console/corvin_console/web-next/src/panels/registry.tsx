/**
 * Panel registry (ADR-0353 P1) — the single list the shell renders from. First
 * entry is vibe-engineering (the reference panel, live via the registry, not a
 * hardcoded <Route>). The other ~40 pages migrate here incrementally (P1-rest);
 * once P3 lands, `requiredCapability`/`requiredFlag` are matched against the
 * backend capability manifest so the nav renders backend-driven.
 */
import { lazy, Suspense } from "react";
import { Loader2 } from "lucide-react";
import { Route } from "react-router-dom";
import type { ConsolePanel } from "./types";

export const PANELS: ConsolePanel[] = [
  {
    id: "vibe-engineering",
    route: "vibe-engineering",
    nav: { label: "Vibe Engineering", icon: "Layers", group: "observability" },
    requiredFlag: "vibe_engineering",
    element: { kind: "react", load: () => import("@/pages/vibe-engineering") },
    contractVersion: "1",
  },
];

export function getPanel(id: string): ConsolePanel | undefined {
  return PANELS.find((p) => p.id === id);
}

/** Render every react-kind panel as a <Route> under /app. Returns an array of
 *  <Route> elements (react-router accepts fragments of routes). */
export function panelRoutes() {
  return PANELS.filter((p) => p.element.kind === "react").map((p) => {
    const el = p.element as Extract<ConsolePanel["element"], { kind: "react" }>;
    const Lazy = lazy(el.load);
    return (
      <Route
        key={p.id}
        path={p.route}
        element={
          <Suspense fallback={<div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin" /></div>}>
            <Lazy />
          </Suspense>
        }
      />
    );
  });
}
