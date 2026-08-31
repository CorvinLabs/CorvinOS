/**
 * api/browser — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── Browser automation (ADR-0182) ───────────────────────────────────────────
export interface BrowserMark { index: number; role: string; name: string; bbox: number[]; }
export interface BrowserObservation { url: string; title: string; marks: BrowserMark[]; }
export interface BrowserAction { action: string; ts?: number; [k: string]: unknown; }
export interface BrowserPending { id: string; action: string; host: string; role: string; name: string; }

export function browserCreateSession(csrf: string, cdpEndpoint?: string): Promise<{ session: string }> {
  return api("/browser/session", { method: "POST", csrf,
    body: cdpEndpoint ? { cdp_endpoint: cdpEndpoint } : undefined });
}

// ── ADR-0200: real-chrome attach controls ────────────────────────────────────
export interface AttachConsent { active: boolean; expires_at: number | null; remaining_s: number }
export function attachConsentStatus(): Promise<AttachConsent> {
  return api("/browser/attach/consent");
}
export function attachConsentGrant(ttlS: number, csrf: string): Promise<AttachConsent> {
  return api("/browser/attach/consent", { method: "POST", csrf, body: { ttl_s: ttlS } });
}
export function attachConsentRevoke(csrf: string): Promise<AttachConsent> {
  return api("/browser/attach/consent", { method: "DELETE", csrf });
}
export function attachLaunchCommand(port = 9222): Promise<{ command: string; port: number }> {
  return api(`/browser/attach/launch-command?port=${port}`);
}
export interface ConfirmMode { mode: "confirm-each" | "watch"; expires_at: number | null; remaining_s: number }
export function confirmModeStatus(): Promise<ConfirmMode> {
  return api("/browser/attach/confirm-mode");
}
export function confirmModeSet(mode: "confirm-each" | "watch", csrf: string, ttlS?: number): Promise<ConfirmMode> {
  return api("/browser/attach/confirm-mode", { method: "POST", csrf,
    body: ttlS != null ? { mode, ttl_s: ttlS } : { mode } });
}
export function browserClose(sid: string, csrf: string): Promise<{ closed: string }> {
  return api(`/browser/${sid}/close`, { method: "POST", csrf });
}
export function browserNavigate(sid: string, url: string, csrf: string): Promise<BrowserObservation> {
  return api(`/browser/${sid}/navigate`, { method: "POST", csrf, body: { url } });
}
export function browserObserve(sid: string, csrf: string): Promise<BrowserObservation> {
  return api(`/browser/${sid}/observe`, { method: "POST", csrf });
}
export function browserClick(sid: string, index: number, csrf: string): Promise<{ ok: boolean }> {
  return api(`/browser/${sid}/click`, { method: "POST", csrf, body: { index } });
}
export function browserFill(sid: string, index: number, text: string, csrf: string): Promise<{ ok: boolean }> {
  return api(`/browser/${sid}/fill`, { method: "POST", csrf, body: { index, text } });
}
export function browserScroll(sid: string, direction: string, csrf: string): Promise<{ ok: boolean }> {
  return api(`/browser/${sid}/scroll`, { method: "POST", csrf, body: { direction } });
}
export function browserActions(sid: string, since: number): Promise<{ actions: BrowserAction[]; pending: BrowserPending[]; next: number }> {
  return api(`/browser/${sid}/actions?since=${since}`);
}
export function browserConfirm(sid: string, id: string, approved: boolean, csrf: string): Promise<{ resolved: boolean }> {
  return api(`/browser/${sid}/confirm`, { method: "POST", csrf, body: { id, approved } });
}
export function browserPause(sid: string, paused: boolean, csrf: string): Promise<{ paused: boolean }> {
  return api(`/browser/${sid}/pause`, { method: "POST", csrf, body: { paused } });
}

export function browserAgent(sid: string, task: string, csrf: string): Promise<{ started: boolean }> {
  return api(`/browser/${sid}/agent`, { method: "POST", csrf, body: { task } });
}
export function browserAgentStop(sid: string, csrf: string): Promise<{ stopped: boolean }> {
  return api(`/browser/${sid}/agent/stop`, { method: "POST", csrf });
}
export function browserAgentContinue(sid: string, csrf: string): Promise<{ resumed: boolean }> {
  return api(`/browser/${sid}/agent/continue`, { method: "POST", csrf });
}
