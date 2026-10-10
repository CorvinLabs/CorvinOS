/**
 * api/auth + dashboard — the console's session and overview endpoints.
 *
 * whoami/logout back the entire auth session (lib/auth.tsx); dashboard and
 * getInstanceIdentity back the Dashboard and Compliance panels. The persona
 * list, the landing persona gallery and the per-persona engine client were
 * removed with the personas themselves (ADR-0537): no route served them and
 * no caller remained.
 */

import { api } from "./client";

// ── Typed endpoints ────────────────────────────────────────────────

export interface WhoamiResponse {
  tier: "owner";
  tenant_id: string;
  fingerprint: string;
  csrf_token: string;
  expires_at: number;
}

export async function whoami(signal?: AbortSignal): Promise<WhoamiResponse> {
  return api<WhoamiResponse>("/auth/whoami", { signal });
}

export async function logout(csrf: string): Promise<void> {
  await api<void>("/auth/logout", { method: "POST", csrf });
}

export interface DashboardBridgeStatus {
  channel: string;
  configured: boolean;
  has_token: boolean;
  source: "canonical" | "legacy" | null;
}

export interface DashboardEngineStatus {
  installed: boolean;
  has_credential: boolean;
}

export interface DashboardResponse {
  tenant_id: string;
  ts: number;
  engine_default: string;
  engine_status: Record<string, DashboardEngineStatus>;
  stt: { mode: "pinned" | "chain"; providers: string[] };
  bridges: DashboardBridgeStatus[];
  audit_chain: {
    present: boolean;
    size_bytes?: number;
    last_event_type?: string | null;
    last_event_ts?: number | null;
  };
  today_counts: Record<string, number>;
  fingerprint: string;
  expires_at: number;
}

export async function dashboard(signal?: AbortSignal): Promise<DashboardResponse> {
  return api<DashboardResponse>("/dashboard", { signal });
}

// ADR-0145 M4 — instance identity / IBC binding status, local-state-only.
export interface InstanceIdentityStatus {
  instance_id: string;
  label: string;
  ibc_bound: boolean;
  plan: string | null;
  email: string | null;
  expires_at: number | null;
  hardware_bound: boolean;
  hardware_matches: boolean | null;
  revocation_status: "revoked" | "clean" | "unknown";
  crl_fetched_at: number | null;
  renewal_due: boolean;
}

export async function getInstanceIdentity(signal?: AbortSignal): Promise<InstanceIdentityStatus> {
  return api<InstanceIdentityStatus>("/settings/instance-identity", { signal });
}
