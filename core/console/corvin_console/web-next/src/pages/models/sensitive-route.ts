/**
 * Sensitive-data route: API calls and the state it is shown in (T-0222, ADR-2268).
 *
 * `GET /settings/engine/sensitive-route` answers the stored route and the providers it may
 * target. The list is never a sample: an install that declared nothing gets an empty list, and
 * the card says so instead of offering a target that would be refused.
 */
import { api } from "@/lib/api/client";

export interface EligibleProvider {
  provider: string;
  label: string;
  locality: "local" | "eu_cloud";
  network_egress: string;
  region: string;
  resource: string;
  admitted_classes: string[];
}

export interface SensitiveRoute {
  enabled: boolean;
  provider: string;
  model_id: string;
  eligible_providers: EligibleProvider[];
}

export const SENSITIVE_ROUTE_KEY = ["settings", "engine", "sensitive-route"] as const;

export function getSensitiveRoute(signal?: AbortSignal): Promise<SensitiveRoute> {
  return api<SensitiveRoute>("/settings/engine/sensitive-route", { signal });
}

export function putSensitiveRoute(
  body: { enabled: boolean; provider: string; model_id: string },
  csrf: string,
): Promise<SensitiveRoute> {
  return api<SensitiveRoute>("/settings/engine/sensitive-route", { method: "PUT", body, csrf });
}

/** What the card shows. `stale` = enabled, but the stored target is no longer eligible. */
export type RouteState = "no_target" | "off" | "on" | "stale";

export function routeState(r: SensitiveRoute): RouteState {
  if (r.eligible_providers.length === 0) return r.enabled ? "stale" : "no_target";
  if (!r.enabled) return "off";
  return r.eligible_providers.some((p) => p.provider === r.provider) ? "on" : "stale";
}

/** Plain-language locality, so a decision-maker does not have to know the enum. */
export function localityLabel(l: EligibleProvider["locality"]): string {
  return l === "local" ? "Local (data stays on this host or network)" : "EU cloud";
}

/** Matches the server's verbatim charset; keeps the Save button honest. */
export const MODEL_ID_RE = /^[A-Za-z0-9._:@/-]{1,200}$/;

export function canSave(provider: string, modelId: string, r: SensitiveRoute): boolean {
  return r.eligible_providers.some((p) => p.provider === provider) && MODEL_ID_RE.test(modelId);
}
