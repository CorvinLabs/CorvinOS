/**
 * api/ldd_quality — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── LDD layer toggles ──────────────────────────────────────────────

export interface LddLayer {
  id: string;
  label: string;
  configured: boolean;
  effective: boolean;
  depends_on: string | null;
}

export interface LddSnapshot {
  layers: LddLayer[];
  master_enabled: boolean;
  presets: string[];
  depends_on: Record<string, string>;
  /** True when the LDD_AUTO_OPTIN=1 env var is active on the server.
   *  In this mode the env var overrides file-based toggles; writes via PUT
   *  take effect on disk but the effective state remains forced-on. */
  auto_optin_active?: boolean;
}

export async function getLdd(signal?: AbortSignal): Promise<LddSnapshot> {
  return api<LddSnapshot>("/ldd", { signal });
}

export async function setLddMaster(
  enabled: boolean,
  csrf: string,
): Promise<LddSnapshot> {
  return api<LddSnapshot>("/ldd/master", {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}

export async function setLddLayer(
  layer: string,
  enabled: boolean,
  csrf: string,
): Promise<LddSnapshot> {
  return api<LddSnapshot>(`/ldd/layers/${encodeURIComponent(layer)}`, {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}

export async function applyLddPreset(
  name: string,
  csrf: string,
): Promise<LddSnapshot> {
  return api<LddSnapshot>(`/ldd/presets/${encodeURIComponent(name)}`, {
    method: "POST",
    csrf,
    body: {},
  });
}

// ── Quality Layers (ADR Gate, etc.) ────────────────────────────────

export interface QualityLayer {
  id: string;
  name: string;
  configured: boolean;
  category: "quality" | "ldd";
}

export interface QualityLayersSnapshot {
  globally_enabled: boolean;
  layers: QualityLayer[];
}

export async function getQualityLayers(signal?: AbortSignal): Promise<QualityLayersSnapshot> {
  return api<QualityLayersSnapshot>("/quality-layers", { signal });
}

export async function setQualityLayerMaster(
  enabled: boolean,
  csrf: string,
): Promise<QualityLayersSnapshot> {
  return api<QualityLayersSnapshot>("/quality-layers/master", {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}

export async function setQualityLayer(
  layer: string,
  enabled: boolean,
  csrf: string,
): Promise<QualityLayersSnapshot> {
  return api<QualityLayersSnapshot>(`/quality-layers/layers/${encodeURIComponent(layer)}`, {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}
