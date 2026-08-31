/**
 * api/settings — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── Global Commands Reference ─────────────────────────────────────

export interface Command {
  name: string;
  description: string;
  syntax?: string;
  details?: string;
  example?: string;
}

export interface CommandsResponse {
  categories: Record<string, Command[]>;
  tip: string;
}

export async function getCommands(signal?: AbortSignal): Promise<CommandsResponse> {
  return api<CommandsResponse>("/setup/commands", { signal });
}

// ── Settings files ─────────────────────────────────────────────────

export async function updateSettingsFile(
  label: string,
  body: string,
  csrf: string,
): Promise<{ ok: true; label: string; path: string }> {
  return api(`/settings/${encodeURIComponent(label)}`, {
    method: "PUT",
    csrf,
    body: { body },
  });
}

// ── Auto-update toggle ─────────────────────────────────────────────

export interface AutoUpdateStatus {
  enabled: boolean;
  path: string;
  configured: boolean;
  version: string;
}

export function getAutoUpdate(signal?: AbortSignal): Promise<AutoUpdateStatus> {
  return api("/settings/auto-update", { signal });
}

export function setAutoUpdate(enabled: boolean, csrf: string): Promise<{ enabled: boolean; ok: boolean }> {
  return api("/settings/auto-update", {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}

// ── Feature flags + worker engine (ship-dark registry) ───────────────

export interface FeatureFlagState {
  id: string;
  label: string;
  description: string;
  owner: string;
  target_release: string;
  tags: string[];
  default: boolean;
  enabled: boolean;
  /** Where the resolved value came from: console overlay, tenant YAML, or the registry default. */
  source: "console" | "tenant_yaml" | "default";
  /**
   * True when switching this flag ON removes the surface that could switch it
   * back OFF — `headless_api_mode` unmounts /console/, so this very panel goes
   * away. The UI must confirm before enabling one of these and must show
   * `recovery_command`. Never hard-code the flag id here: the backend registry
   * decides which flags are self-locking.
   */
  self_locking: boolean;
  /** Console-independent off-ramp, e.g. `corvin config set features.x false`. Null unless self_locking. */
  recovery_command: string | null;
}

export function getFeatureFlags(signal?: AbortSignal): Promise<{ features: FeatureFlagState[] }> {
  return api("/settings/features", { signal });
}

export function setFeatureFlag(
  id: string,
  enabled: boolean,
  csrf: string,
): Promise<{ id: string; enabled: boolean; ok: boolean }> {
  return api(`/settings/features/${encodeURIComponent(id)}`, {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}


export type WorkerEngineMode = "native" | "acs" | "tde";

export interface WorkerEngineStatus {
  mode: WorkerEngineMode;
  modes: WorkerEngineMode[];
  default: WorkerEngineMode;
}

export function getWorkerEngine(signal?: AbortSignal): Promise<WorkerEngineStatus> {
  return api("/settings/worker-engine", { signal });
}

export function setWorkerEngine(
  mode: WorkerEngineMode,
  csrf: string,
): Promise<{ mode: WorkerEngineMode; ok: boolean }> {
  return api("/settings/worker-engine", {
    method: "PUT",
    csrf,
    body: { mode },
  });
}

// ── Always-on service tier (ADR-0184 Stufe 2) ────────────────────────

export interface ServiceTierStatus {
  available: boolean;
  always_on: boolean;
  raw_status: string | null;
}

export interface ServiceTierChangeResult extends ServiceTierStatus {
  applied: boolean;
  manual_command: string | null;
  detail: string | null;
}

export function getServiceTier(signal?: AbortSignal): Promise<ServiceTierStatus> {
  return api("/settings/service-tier", { signal });
}

export function setServiceTier(enabled: boolean, csrf: string): Promise<ServiceTierChangeResult> {
  return api("/settings/service-tier", {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}

// ── Delegation budget settings ──────────────────────────────────────

export interface DelegationBudgetMeta {
  min: number;
  max: number;
  default: number;
}

export interface DelegationBudgetResponse {
  values: {
    timeout_seconds: number;
    max_worker_turns: number;
    max_loops: number;
    max_wall_time: number;
    max_total_workers: number;
    max_depth: number;
  };
  meta: Record<string, DelegationBudgetMeta>;
  path: string;
}

export function getDelegationBudget(signal?: AbortSignal): Promise<DelegationBudgetResponse> {
  return api("/settings/delegation-budget", { signal });
}

export function setDelegationBudget(
  values: Partial<DelegationBudgetResponse["values"]>,
  csrf: string,
): Promise<{ values: DelegationBudgetResponse["values"]; ok: boolean }> {
  return api("/settings/delegation-budget", { method: "PUT", csrf, body: values });
}

// ── Self-healing config (ACO L5 toggles + healing telemetry) ────────

export interface HealingConfigResponse {
  telemetry_enabled: boolean;   // healing traces (spec.telemetry.healing_traces)
  ping_enabled: boolean;        // anonymous instance ping (spec.telemetry.ping_enabled)
  error_enabled: boolean;       // error diagnostics (spec.telemetry.error_traces)
  healing_enabled: boolean;     // ACO L5 self-healing (spec.aco.l5_enabled)
  risky_enabled: boolean;       // risky repair tier (spec.aco.l5_risky)
}

export function getHealingConfig(signal?: AbortSignal): Promise<HealingConfigResponse> {
  return api("/healing-config", { signal });
}

export function setHealingConfig(
  patch: Partial<HealingConfigResponse>,
  csrf: string,
): Promise<HealingConfigResponse> {
  return api("/healing-config", { method: "PATCH", csrf, body: patch });
}
