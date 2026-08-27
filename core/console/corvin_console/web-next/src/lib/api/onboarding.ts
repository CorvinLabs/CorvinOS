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

export async function postSetupComplete(csrf: string): Promise<{ ok: boolean }> {
  return api("/setup/complete", { method: "POST", csrf });
}

export async function postTestEngine(
  engine_id: string,
  csrf: string,
  signal?: AbortSignal,
): Promise<{ ok: boolean; detail: string; steps?: string[]; platform?: string; download_url?: string }> {
  return api("/setup/test-engine", { method: "POST", body: { engine_id }, csrf, signal });
}

// ── First-boot spoken onboarding self-check ──────────────────────────
// docs/first-run-language-and-voice-onboarding.md §2. A Hermes warm-up or a
// cold STT model load can take tens of seconds, so the server runs the
// check in a background thread — same async-job/poll shape as
// startWhatsappBridge above.

export interface WelcomeCheckComponent {
  status: "ok" | "degraded" | "unavailable";
  detail: string;
}

export interface WelcomeCheckResult {
  state: "idle" | "running" | "done";
  lang?: string;
  components?: Record<string, WelcomeCheckComponent>;
  greeting?: string;
}

export async function postWelcomeCheck(csrf: string): Promise<WelcomeCheckResult> {
  return api("/setup/welcome-check", { method: "POST", csrf, timeoutMs: 20_000 });
}

export async function getWelcomeCheckStatus(signal?: AbortSignal): Promise<WelcomeCheckResult> {
  return api("/setup/welcome-check/status", { signal, timeoutMs: 15_000 });
}

/**
 * Kick off the self-check and poll it to completion. Never rejects on a
 * degraded/unavailable component — only a hard network/timeout failure
 * throws, and the caller (WelcomeStep) treats even that as non-fatal:
 * onboarding always proceeds regardless of outcome.
 */
export async function runWelcomeCheck(csrf: string): Promise<WelcomeCheckResult> {
  await postWelcomeCheck(csrf);
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  // Backend runs its checks concurrently (bounded by the slowest single
  // check — a cold Hermes warm-up at up to 60s), not sequentially. Poll for
  // 100s, comfortably above that worst case, so a cold/default install
  // doesn't give up here and silently lose the spoken greeting.
  for (let i = 0; i < 100; i++) {
    const s = await getWelcomeCheckStatus();
    if (s.state === "done") return s;
    await sleep(1_000);
  }
  return { state: "running" };
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
