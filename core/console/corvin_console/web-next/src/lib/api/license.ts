/**
 * api/license — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { ApiError, BASE, api } from "./client";

// ── License management (ADR-0017 Phase IV) ────────────────────────────

export interface LicenseStatus {
  tier: string;
  mode: "free" | "active" | "grace" | "expired" | "invalid";
  expires_at: number | null;
  grace_ends_at: number | null;
  customer_fp: string | null;
  feature_flags: string[];
}

export async function getLicenseStatus(signal?: AbortSignal): Promise<LicenseStatus> {
  return api<LicenseStatus>("/license/status", { signal });
}

/** ADR-0092 full licence state — limits, features, custom per-customer config. */
export interface LicenseInfo {
  tier: string;
  loaded: boolean;
  issued_to: string | null;
  expires_at: number | null;
  subscription_active_until: number | null;
  jti_prefix: string | null;
  limits: Record<string, number | string[] | boolean | null>;
  features: Record<string, boolean>;
  custom: Record<string, unknown>;
  free_tier: Record<string, number | string[] | boolean | null>;
}

export async function getLicenseInfo(signal?: AbortSignal): Promise<LicenseInfo> {
  return api<LicenseInfo>("/license/info", { signal });
}

export interface LicenseUploadResponse {
  ok: boolean;
  tier: string;
  customer_fp: string;
  expires_at: number;
}

export async function uploadLicense(
  file: File,
  csrf: string,
): Promise<LicenseUploadResponse> {
  const form = new FormData();
  form.append("file", file);

  const headers: Record<string, string> = {
    "X-CSRF-Token": csrf,
  };

  const res = await fetch(`${BASE}/license/upload`, {
    method: "POST",
    headers,
    credentials: "include",
    body: form,
  });

  const text = await res.text();
  let payload: unknown = text;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      /* keep as text */
    }
  }

  if (!res.ok) {
    throw new ApiError(res.status, payload);
  }
  return payload as LicenseUploadResponse;
}

export async function revokeLicense(
  reason: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>("/license/revoke", {
    method: "POST",
    body: { reason },
    csrf,
  });
}

export interface LicenseKeyResponse {
  ok: boolean;
  tier: string;
  loaded: boolean;
  issued_to: string | null;
  expires_at: number | null;
}

export async function applyLicenseKey(
  key: string,
  csrf: string,
): Promise<LicenseKeyResponse> {
  return api<LicenseKeyResponse>("/license/key", {
    method: "POST",
    body: { key },
    csrf,
  });
}

export interface LicenseAuditEvent {
  timestamp: number;
  event_type: string;
  details: Record<string, unknown>;
}

export async function getLicenseAudit(
  limit?: number,
  signal?: AbortSignal,
): Promise<LicenseAuditEvent[]> {
  const query = limit ? `?limit=${limit}` : "";
  return api<LicenseAuditEvent[]>(`/license/audit-tail${query}`, { signal });
}
