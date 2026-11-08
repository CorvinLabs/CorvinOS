/**
 * The inspector's decisions, kept pure so they are unit-tested: what a feature row says, what the server's
 * 409 means for the user, and which tabs exist. The page only draws these.
 */
import { ApiError } from "@/lib/api/client";
import type { FeatureRow } from "@/lib/api/projects";
import { featureLabel } from "./encodings";

export type Tab = "overview" | "structure" | "docs" | "knowledge" | "tasks" | "videos" | "directory" | "compute" | "features" | "settings";

/** Each tab, and the feature it needs (null = always there). A tab whose feature is off shows "Feature off". */
export const TABS: { id: Tab; label: string; feature: string | null }[] = [
  { id: "overview", label: "Overview", feature: null },
  { id: "structure", label: "Structure", feature: null },
  { id: "docs", label: "Docs", feature: "kb" },
  { id: "knowledge", label: "Knowledge", feature: "graph" },
  { id: "tasks", label: "Tasks", feature: "tasks" },
  { id: "videos", label: "Videos", feature: "videos" },
  { id: "directory", label: "Directory", feature: "directory" },
  { id: "compute", label: "Compute", feature: "acs" },
  { id: "features", label: "Features", feature: null },
  { id: "settings", label: "Settings", feature: null },
];

export function parseTab(v: string | null): Tab {
  return TABS.some((t) => t.id === v) ? (v as Tab) : "overview";
}

/** Whether a tab's content may be shown, and which feature to offer when it may not. */
export function tabGate(tab: Tab, active: string[]): { open: boolean; feature: string | null } {
  const t = TABS.find((x) => x.id === tab);
  if (!t || t.feature === null) return { open: true, feature: null };
  return { open: active.includes(t.feature), feature: t.feature };
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

/** The same rule the server enforces (VIDEO_NAME_RE): shown before the request, never instead of it. */
export function videoNameProblem(name: string): string | null {
  const n = name.trim();
  if (n.length === 0) return "Give the video a name.";
  if (n.length > 60) return "The name is at most 60 characters.";
  if (!/^[a-z0-9][a-z0-9-]*$/.test(n)) return "Use lowercase letters, digits and dashes, starting with a letter or digit.";
  return null;
}
