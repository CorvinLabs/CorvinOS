/**
 * api/onboarding — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── ADR-0062: Setup Gate ────────────────────────────────────────────

export interface SetupStatus {
  first_run: boolean;
  engine_connected: boolean;
  claude_cli_ok: boolean;
  anthropic_key_set: boolean;
  bridges_configured: string[];
  setup_complete: boolean;
}

export async function getSetupStatus(signal?: AbortSignal): Promise<SetupStatus> {
  return api("/setup/status", { signal });
}

// ── ADR-0062: Console Assistant ─────────────────────────────────────

export interface AssistantHistoryEntry {
  role: "user" | "assistant";
  content: string;
}

export interface AssistantContext {
  current_page?: string;
  setup_status?: Partial<SetupStatus>;
  license_tier?: string;
  personas?: string[];
  language?: string;   // detected UI language, e.g. "de" | "en"
}

export async function postAssistantMessage(
  message: string,
  context: AssistantContext,
  csrf: string,
  history?: AssistantHistoryEntry[],
): Promise<{ ok: boolean; response: string }> {
  return api("/assistant/message", {
    method: "POST",
    body: { message, context, history: history ?? [] },
    csrf,
  });
}

export async function getAssistantPing(signal?: AbortSignal): Promise<{ available: boolean; version: string | null }> {
  return api("/assistant/ping", { signal });
}
