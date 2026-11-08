/**
 * The inspector's decisions, kept pure so they are unit-tested: what a feature row says, what the server's
 * 409 means for the user, and which tabs exist. The page only draws these.
 */
import { ApiError } from "@/lib/api/client";
import type { FeatureRow } from "@/lib/api/projects";
import { featureLabel } from "./encodings";

export type Tab = "overview" | "features" | "settings";
export const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "features", label: "Features" },
  { id: "settings", label: "Settings" },
];
export function parseTab(v: string | null): Tab {
  return TABS.some((t) => t.id === v) ? (v as Tab) : "overview";
}

/** What one feature row offers: switch on/off, and why it cannot be switched on yet. */
export interface RowState {
  label: string;
  canToggle: boolean;
  action: "on" | "off";
  /** Shown when the feature is off: the requirements that would be pulled in when it is switched on. */
  preview: string[];
  blockedBy: string | null;
}

export function rowState(f: FeatureRow): RowState {
  const missingPlugins = f.plugins.filter((p) => p.state !== "enabled");
  const blockedBy = missingPlugins.length
    ? `needs the ${missingPlugins.map((p) => p.plugin.replace(/_/g, " ")).join(", ")} plugin, which is not enabled for this tenant`
    : null;
  return {
    label: featureLabel(f.id),
    canToggle: f.id !== "core" && blockedBy === null,
    action: f.active ? "off" : "on",
    preview: f.active ? [] : f.requires.filter((r) => r !== "core").map(featureLabel),
    blockedBy,
  };
}

/** The server's 409 for a feature change, turned into the choice the user has to make. */
export type Conflict =
  | { kind: "dependants"; feature: string; dependants: string[] }
  | { kind: "requires"; requires: string[] }
  | { kind: "plugin"; message: string }
  | { kind: "other"; message: string };

export function conflictOf(err: unknown): Conflict | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.detail as { detail?: Record<string, unknown> } | undefined;
  const d = (body?.detail ?? {}) as Record<string, unknown>;
  const reason = typeof d.reason_class === "string" ? d.reason_class : "";
  const message = typeof d.message === "string" ? d.message : "The change was refused.";
  if (reason === "has_dependants" && Array.isArray(d.dependants)) {
    return { kind: "dependants", feature: String(d.feature ?? ""), dependants: (d.dependants as string[]).map(featureLabel) };
  }
  if (reason === "needs_dependencies" && Array.isArray(d.requires)) {
    return { kind: "requires", requires: (d.requires as string[]).map(featureLabel) };
  }
  if (reason === "plugin_missing") return { kind: "plugin", message };
  return { kind: "other", message };
}

/** The Overview's headline from the checks: never "all good" while a check is red. */
export function healthHeadline(checks: { ok: boolean }[]): { text: string; ok: boolean } {
  const failing = checks.filter((c) => !c.ok).length;
  if (checks.length === 0) return { text: "No checks have run.", ok: false };
  if (failing === 0) return { text: "Every check passes.", ok: true };
  return { text: `${failing} of ${checks.length} checks need attention.`, ok: false };
}
