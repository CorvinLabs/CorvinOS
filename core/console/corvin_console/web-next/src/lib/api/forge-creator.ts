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
  panel: { title: string; entry: string } | null;
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
  request_chars: number | null;
  findings: ForgeFindingOut[];
  warnings: string[];
  egress_hosts: string[];
  engine: string | null;
  panel: { title: string; entry: string } | null;
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

/** Prepended to a previewed panel: no network, no external resources, inline code only.
 *  The HTML lint at generation time is advisory; this policy is what holds. */
export const FORGED_PANEL_CSP =
  '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; ' +
  "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; form-action 'none'\">";

/** Runs before any of the untrusted HTML parses. `allow-scripts` without
 *  `allow-top-navigation`/`allow-popups` stops the panel from navigating the
 *  parent or opening a window, but self-navigation (`location.href = ...`,
 *  `location.assign/replace`, a clicked `<a href>`, a `<meta refresh>`) is
 *  unaffected by either the sandbox or the CSP's `default-src` — a same-frame
 *  redirect can still beacon data out. Best-effort in-frame hardening here;
 *  the authoritative stop is the parent's load-count guard (see forged.tsx),
 *  which blanks the frame the moment ANY re-navigation fires a second `load`. */
const FORGED_PANEL_LOCKDOWN =
  "<script>(function(){" +
  'try{var noop=function(){};location.assign=noop;location.replace=noop;' +
  "Object.defineProperty(location,'href',{set:noop,get:function(){return '';}});}catch(e){}" +
  "document.addEventListener('click',function(ev){" +
  "var a=ev.target&&ev.target.closest&&ev.target.closest('a[href]');" +
  "if(a&&!(a.getAttribute('href')||'').startsWith('#')){ev.preventDefault();ev.stopPropagation();}" +
  "},true);" +
  "new MutationObserver(function(muts){muts.forEach(function(m){m.addedNodes.forEach(function(n){" +
  "if(n.tagName==='META'&&/refresh/i.test(n.getAttribute('http-equiv')||''))n.remove();});});})" +
  ".observe(document.documentElement,{childList:true,subtree:true});" +
  "})();</script>";

export function previewDocument(html: string): string {
  // After a leading doctype (keeps standards mode); the parser hoists the meta into <head>.
  const prefix = FORGED_PANEL_CSP + FORGED_PANEL_LOCKDOWN;
  const doctype = /^\s*<!doctype[^>]*>/i.exec(html);
  if (doctype) return doctype[0] + prefix + html.slice(doctype[0].length);
  return prefix + html;
}
