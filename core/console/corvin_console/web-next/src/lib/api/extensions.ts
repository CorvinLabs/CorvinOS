/**
 * api/extensions — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── ADR-0142: Layer Extensions ─────────────────────────────────────────────

export interface CoreLayer {
  name: string;
  version: string;
  active: boolean;
  core: true;
  description: string;
}

export interface ExtensionHookDecl {
  event: string;
  script: string;
  priority: number;
}

export interface ExtensionManifest {
  name: string;
  version: string;
  description: string;
  author: string;
  license: string;
  scope: string;
  hooks: ExtensionHookDecl[];
  provides: { name: string; version?: string }[];
  requires: string[];
  mcp_tools: unknown[];
  enabled: boolean;
}

export interface ExtensionList {
  core: CoreLayer[];
  extensions: ExtensionManifest[];
}

export interface ExtensionValidateResult {
  ok: boolean;
  name?: string;
  version?: string;
  scope?: string;
  hooks?: number;
  requires?: number;
  error?: string;
}

export async function listExtensions(signal?: AbortSignal): Promise<ExtensionList> {
  return api<ExtensionList>("/extensions", { signal });
}

export async function getExtension(
  name: string,
  signal?: AbortSignal,
): Promise<ExtensionManifest & { core?: boolean; removable?: boolean }> {
  return api(`/extensions/${encodeURIComponent(name)}`, { signal });
}

export async function installExtension(
  source: string,
  csrf: string,
  opts: { scope?: string; enable?: boolean } = {},
): Promise<{ name: string; version: string; scope: string; enabled: boolean }> {
  return api("/extensions", {
    method: "POST",
    body: { source, scope: opts.scope ?? null, enable: opts.enable ?? false },
    csrf,
  });
}

export async function setExtensionEnabled(
  name: string,
  enabled: boolean,
  csrf: string,
): Promise<{ name: string; version: string; scope: string; enabled: boolean }> {
  return api(`/extensions/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: { enabled },
    csrf,
  });
}

export async function removeExtension(name: string, csrf: string): Promise<void> {
  return api<void>(`/extensions/${encodeURIComponent(name)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function validateExtensionManifest(
  manifestYaml: string,
  csrf: string,
  signal?: AbortSignal,
): Promise<ExtensionValidateResult> {
  // POST → CSRF token required (backend: require_csrf on every mutation-shaped route).
  return api<ExtensionValidateResult>("/extensions/validate", {
    method: "POST",
    csrf,
    body: { manifest_yaml: manifestYaml },
    signal,
  });
}
