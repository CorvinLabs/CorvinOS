/**
 * api/personas — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
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

export interface PersonaSummary {
  name: string;
  source: "bundle" | "user";
  description: string;
  permission_mode: string | null;
  default_engine: string | null;
  engine?: string | null;
  os_model?: string | null;
  worker_model?: string | null;
  engine_lock?: boolean;
  model: string | null;
  tool_namespace: string | null;
  forge_enabled: boolean;
  skill_forge_enabled: boolean;
  inject_skills?: boolean;
  ldd_preset: string | null;
  mcp_count: number;
  tools_allowed: number;
  tools_disallowed: number;
  disabled?: boolean;
  path: string;
}

export interface PersonaListResponse {
  tenant_id: string;
  count: number;
  personas: PersonaSummary[];
}

export async function listPersonas(signal?: AbortSignal): Promise<PersonaListResponse> {
  return api<PersonaListResponse>("/personas", { signal });
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
}

export async function getInstanceIdentity(signal?: AbortSignal): Promise<InstanceIdentityStatus> {
  return api<InstanceIdentityStatus>("/settings/instance-identity", { signal });
}

// Landing-personas is a NEW endpoint (Iteration 1, task 5) — exposed
// unauthenticated so the public hero can render the gallery without a
// login. Backend returns only the curated "publishable" projection
// (name + description + tool_namespace + ldd_preset + forge_enabled).
export interface LandingPersona {
  name: string;
  description: string;
  tool_namespace: string | null;
  forge_enabled: boolean;
  skill_forge_enabled: boolean;
  ldd_preset: string | null;
}

export interface LandingPersonasResponse {
  count: number;
  personas: LandingPersona[];
}

export async function landingPersonas(signal?: AbortSignal): Promise<LandingPersonasResponse> {
  return api<LandingPersonasResponse>("/landing/personas", { signal });
}

// ── Persona detail / mutations ─────────────────────────────────────

export interface PersonaDetailResponse {
  name: string;
  source: "bundle" | "user";
  path: string;
  body: Record<string, unknown>;
  editable: boolean;
  disabled?: boolean;
}

export async function getPersona(
  name: string,
  signal?: AbortSignal,
): Promise<PersonaDetailResponse> {
  return api<PersonaDetailResponse>(`/personas/${encodeURIComponent(name)}`, { signal });
}

export async function updatePersona(
  name: string,
  body: Record<string, unknown>,
  csrf: string,
): Promise<{ name: string; source: "user"; path: string; ok: true }> {
  return api(`/personas/${encodeURIComponent(name)}`, {
    method: "PUT",
    csrf,
    body: { body },
  });
}

export async function copyPersonaFromBundle(
  name: string,
  csrf: string,
): Promise<{ name: string; copied: boolean; path: string; ok: true }> {
  return api(`/personas/${encodeURIComponent(name)}/copy-from-bundle`, {
    method: "POST",
    csrf,
  });
}

// Create a brand-new user-scope persona. The backend PUT is create-or-replace,
// so creation reuses it — the body just carries a fresh name not present in the
// bundle or user dir.
export async function createPersona(
  name: string,
  body: Record<string, unknown>,
  csrf: string,
): Promise<{ name: string; source: "user"; ok: true }> {
  return api(`/personas/${encodeURIComponent(name)}`, {
    method: "PUT",
    csrf,
    body: { body },
  });
}

export async function deletePersona(
  name: string,
  csrf: string,
): Promise<{ name: string; deleted: boolean; reverted_to_bundle: boolean; ok: true }> {
  return api(`/personas/${encodeURIComponent(name)}`, {
    method: "DELETE",
    csrf,
  });
}

// Deactivate / reactivate a persona — name-level per-tenant registry, so it
// works for bundle personas too (no file copy). A disabled persona is dropped
// from runtime auto-routing and shown "off" in the console.
export async function setPersonaDisabled(
  name: string,
  disabled: boolean,
  csrf: string,
): Promise<{ name: string; disabled: boolean; ok: true }> {
  const action = disabled ? "disable" : "enable";
  return api(`/personas/${encodeURIComponent(name)}/${action}`, {
    method: "POST",
    csrf,
  });
}
