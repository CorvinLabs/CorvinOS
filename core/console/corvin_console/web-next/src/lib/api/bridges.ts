/**
 * api/bridges — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── Bridges ────────────────────────────────────────────────────────

export interface BridgeListItem {
  channel: string;
  /** True only when a usable credential is on disk — NOT merely "a settings
   *  file exists". A file holding just preferences is not a connection. */
  configured: boolean;
  /** A settings file exists (possibly preferences-only). Used to tell
   *  "never set up" apart from "disconnected, preferences kept". */
  has_settings?: boolean;
  enabled: boolean;
  path: string;
  size_bytes: number;
}

export interface BridgeListResponse {
  count: number;
  bridges: BridgeListItem[];
}

export async function listBridges(signal?: AbortSignal): Promise<BridgeListResponse> {
  return api<BridgeListResponse>("/bridges", { signal });
}

export interface BridgeSettingsResponse {
  channel: string;
  path: string;
  exists: boolean;
  settings: Record<string, unknown>;
}

export async function getBridgeSettings(
  channel: string,
  signal?: AbortSignal,
): Promise<BridgeSettingsResponse> {
  return api<BridgeSettingsResponse>(
    `/bridges/${encodeURIComponent(channel)}/settings`,
    { signal },
  );
}

export async function putBridgeSettings(
  channel: string,
  settings: Record<string, unknown>,
  csrf: string,
  reAuthToken?: string,
): Promise<{ channel: string; path: string; ok: true }> {
  return api(`/bridges/${encodeURIComponent(channel)}/settings`, {
    method: "PUT",
    csrf,
    body: { settings, re_auth_token: reAuthToken || null },
  });
}

export interface BridgeEnabledResponse {
  channel: string;
  enabled: boolean;
  restart_needed: boolean;
  supervisor: { applied: boolean; via?: string; reason?: string; output?: string };
  ok: true;
}

export async function setBridgeEnabled(
  channel: string,
  enabled: boolean,
  csrf: string,
  reAuthToken?: string,
): Promise<BridgeEnabledResponse> {
  return api<BridgeEnabledResponse>(
    `/bridges/${encodeURIComponent(channel)}/enabled`,
    {
      method: "PUT",
      csrf,
      body: { enabled, re_auth_token: reAuthToken || null },
    },
  );
}

/** Drop a channel's connection so it can be set up again.
 *
 *  "disconnect" strips credentials and pairing state but keeps preferences
 *  (whitelist, PIN, rate limits, chat_profiles) — the "reconnect with a
 *  different bot" path. "delete" removes settings.json entirely; a .bak is
 *  kept server-side either way. Both stop the daemon and disable the channel. */
export interface BridgeDisconnectResponse {
  channel: string;
  mode: "disconnect" | "delete";
  /** Key NAMES that were removed — never their values. */
  cleared_keys: string[];
  removed_files: string[];
  archived_state: string[];
  restart_needed: boolean;
  ok: boolean;
}

export async function disconnectBridge(
  channel: string,
  mode: "disconnect" | "delete",
  csrf: string,
  reAuthToken?: string,
): Promise<BridgeDisconnectResponse> {
  return api<BridgeDisconnectResponse>(
    `/bridges/${encodeURIComponent(channel)}/disconnect`,
    {
      method: "POST",
      csrf,
      body: { mode, re_auth_token: reAuthToken || null },
    },
  );
}


export interface BridgeSetupInfo {
  channel: string;
  configured: boolean;
  current_token_masked: string;
  qr_available: boolean;
  qr_url: string | null;
  guide: {
    display: string;
    steps: string[];
    field_label: string | null;
    field_placeholder: string | null;
    token_key: string | null;
    setup_url: string | null;
  };
}

export async function getBridgeSetup(channel: string, signal?: AbortSignal): Promise<BridgeSetupInfo> {
  return api<BridgeSetupInfo>(`/setup/bridge/${encodeURIComponent(channel)}`, { signal });
}

export interface WhatsappStartResult {
  ok: boolean;
  pid?: number;
  already_running?: boolean;
  node_missing?: boolean;
  error?: string;
  node_steps?: { platform: string; download_url: string; steps: string[] };
}

interface WhatsappStartStatus {
  state: "idle" | "running" | "done" | "error";
  phase?: string;
  result?: WhatsappStartResult;
}

/**
 * Start the WhatsApp bridge daemon from the UI (installs Node.js + deps on
 * demand). The daemon start + npm install can take a minute, so the server runs
 * it in a background thread; this starts it then polls the status to a terminal
 * state. `onPhase` receives live phase strings. Once the daemon is up, the QR
 * appears via the /setup/bridge/whatsapp poll (qr_available → <img>).
 */
export async function startWhatsappBridge(
  csrf: string,
  onPhase?: (phase: string) => void,
): Promise<WhatsappStartResult> {
  await api<WhatsappStartStatus>("/setup/whatsapp/start", {
    method: "POST",
    csrf,
    timeoutMs: 20_000,
  });
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 200; i++) {
    await sleep(2_000);
    let s: WhatsappStartStatus;
    try {
      s = await api<WhatsappStartStatus>("/setup/whatsapp/start/status", { timeoutMs: 15_000 });
    } catch {
      continue;
    }
    if (s.phase) onPhase?.(s.phase);
    if (s.state === "done" || s.state === "error") {
      return s.result ?? { ok: s.state === "done", error: s.state === "error" ? "Start failed" : undefined };
    }
  }
  return { ok: false, error: "Timed out starting the WhatsApp bridge — check that Node.js is installed." };
}
