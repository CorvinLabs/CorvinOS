/**
 * api/personas — extracted from the former monolithic lib/api.ts.
 *
 * The dedicated Personas editor panel (pages/personas.tsx) was deleted
 * 2026-09-10 (ADR-0400 consolidation) — the persona-detail CRUD functions
 * that ONLY that panel used (getPersona/updatePersona/copyPersonaFromBundle/
 * createPersona/deletePersona/setPersonaDisabled) are gone with it. The rest
 * of this module is NOT persona-panel-specific: whoami/logout back the
 * console's entire auth session (lib/auth.tsx), dashboard/getInstanceIdentity
 * back the Dashboard + Compliance panels, listPersonas backs the Cowork
 * persona picker, and landingPersonas backs the public landing gallery.
 * A 2026-09-10 cleanup pass stubbed all of those out as empty/fake data
 * believing the whole file was personas-panel-only — restored here.
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

// Landing-personas is exposed unauthenticated so the public hero can render
// the gallery without a login. Backend returns only the curated
// "publishable" projection (name + description + tool_namespace + ldd_preset
// + forge_enabled).
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
