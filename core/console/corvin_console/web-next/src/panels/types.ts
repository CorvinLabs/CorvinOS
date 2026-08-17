/**
 * Console panel contract (ADR-0353 P1) — a panel is a plugin. The shell renders
 * panels from the registry instead of hardcoding <Route>s. First-party panels are
 * lazy React components; the framework-agnostic kinds (web-component/iframe) land
 * with P4/P7 for community panels.
 */
import type { ComponentType } from "react";

export interface ConsolePanel {
  /** stable id, e.g. "vibe-engineering" */
  id: string;
  /** route relative to /app (no leading slash), e.g. "vibe-engineering" */
  route: string;
  nav: { label: string; icon: string; group?: string; order?: number };
  /** gate: only mount when the backend capability manifest reports it (P3) */
  requiredCapability?: string;
  /** gate: only mount when this feature flag is on */
  requiredFlag?: string;
  element:
    | { kind: "react"; load: () => Promise<{ default: ComponentType }> }
    | { kind: "web-component"; tag: string; src: string }
    | { kind: "iframe"; src: string; sandbox: string };
  /** checked against the capability manifest's contract version (P3) */
  contractVersion: string;
}
