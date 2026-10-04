/**
 * Tool Forge + Plugin Forge (routes/forge_creator.py). Same run/poll protocol
 * as Skill Forge's /skill-creator: POST accepts a run, the panel polls its
 * tenant-bound status until success or failed.
 */
import { api } from "./client";

export type ForgeKind = "tool" | "plugin";

export interface ForgeFindingOut {
  dimension: string;
  summary: string;
  verdict: string;
}

export interface ToolTestCase {
  index: number;
  input: unknown;
  expect: unknown;
  output?: unknown;
  error?: string;
  passed: boolean;
  sandbox?: string;
}

export interface ForgedTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  tests: { cases: ToolTestCase[]; passed: number; total: number };
  iterations: number;
  quality: number;
  findings: ForgeFindingOut[];
  sandbox: string[];
  registry_path?: string;
}

export interface ForgedPluginResult {
  plugin_id: string;
  dirname: string;
  display_name: string;
  kind: string;
  tier: string;
  quality: number | null;
  findings: ForgeFindingOut[];
  files: string[];
  panel: { title: string; entry: string; sandbox: string[] } | null;
  review_skipped: boolean;
}

export interface ForgeRunStatus {
  run_id: string;
  kind: ForgeKind;
  status: "running" | "success" | "failed";
  phase: string;
  phases: string[];
  progress: number;
  message: string;
  engine: string;
  error: string | null;
  tool: ForgedTool | null;
  plugin: ForgedPluginResult | null;
}

export interface ForgedPluginSummary {
  dirname: string;
  plugin_id: string;
  display_name: string;
  kind: string;
  tier: string;
  quality: number | null;
  review_skipped: boolean;
  risk_flags: string[];
  created_at: number | null;
  has_panel: boolean;
  installed: false;
  origin_on_install: "community";
  signed: false;
}

export interface ForgedPluginDetail extends ForgedPluginSummary {
  request: string | null;
  findings: ForgeFindingOut[];
  warnings: string[];
  egress_hosts: string[];
  engine: string | null;
  panel: { title: string; entry: string; sandbox: string[] } | null;
  panel_html: string | null;
  files: { path: string; size: number; content?: string }[];
}

export function startForgeGeneration(
  kind: ForgeKind,
  userRequest: string,
  csrf: string,
  panelRequest = "",
): Promise<{ status: string; run_id: string; kind: ForgeKind; message: string }> {
  return api(`/forge-creator/${kind}/generate`, {
    method: "POST",
    csrf,
    body: {
      user_request: userRequest,
      ...(kind === "plugin" && panelRequest.trim() ? { panel_request: panelRequest.trim() } : {}),
    },
  });
}

export function getForgeRunStatus(runId: string, signal?: AbortSignal): Promise<ForgeRunStatus> {
  return api<ForgeRunStatus>(`/forge-creator/status/${encodeURIComponent(runId)}`, { signal });
}

export function listForgedPlugins(signal?: AbortSignal): Promise<{ plugins: ForgedPluginSummary[]; count: number }> {
  return api(`/forge-creator/plugins`, { signal });
}

export function getForgedPlugin(dirname: string, signal?: AbortSignal): Promise<ForgedPluginDetail> {
  return api<ForgedPluginDetail>(`/forge-creator/plugins/${encodeURIComponent(dirname)}`, { signal });
}

export function deleteForgedPlugin(dirname: string, csrf: string): Promise<{ ok: boolean }> {
  return api(`/forge-creator/plugins/${encodeURIComponent(dirname)}`, { method: "DELETE", csrf });
}

/** The ONLY sandbox a generated (community) panel preview gets — never allow-same-origin (ADR-2189 D2). */
export const FORGED_PANEL_SANDBOX = "allow-scripts";
