/**
 * Personas API stub — panel deleted 2026-09-10
 * This file kept for backward compatibility; all functions return empty stubs.
 */

export async function listPersonas(_signal?: AbortSignal) {
  return { personas: [] };
}

export async function landingPersonas(_signal?: AbortSignal) {
  return { personas: [] };
}

export interface LandingPersona {
  id: string;
  name: string;
}

export interface DashboardPersona {
  id: string;
  name: string;
}

export async function logout(_csrfToken: string) {
  return;
}

export async function whoami(_signal?: AbortSignal) {
  return {
    csrf_token: "",
    tenant_id: "_default",
    tier: "standard",
    fingerprint: undefined,
  };
}

export interface WhoamiResponse {
  csrf_token: string;
  tenant_id: string;
  tier: string;
  fingerprint?: string;
}

export async function dashboard(_signal?: AbortSignal) {
  return {
    data: {
      engine_status: { status: "ok" },
      ts: new Date().toISOString(),
      audit_chain: [],
      bridges: [],
      today_counts: { total: 0 },
    },
  };
}

export interface InstanceIdentityStatus {
  status: string;
  instance_id?: string;
  label?: string;
  ibc_bound?: boolean;
  plan?: string;
  hardware_bound?: boolean;
  hardware_matches?: boolean;
  revocation_status?: string;
}

export async function getInstanceIdentity(_signal?: AbortSignal) {
  return {
    status: "unknown",
    instance_id: undefined,
    label: undefined,
    ibc_bound: false,
    plan: undefined,
    hardware_bound: false,
    hardware_matches: false,
    revocation_status: undefined,
  } as InstanceIdentityStatus;
}
