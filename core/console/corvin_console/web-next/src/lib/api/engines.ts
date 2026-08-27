/**
 * api/engines — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── OS Engine selector (ADR-0067 M2.4) ────────────────────────────

// Per-engine model config (ADR-0119)
export interface EngineModelConfig {
  os_model: string | null;
  worker_model: string | null;
  // ADR-0181 — model provider id (anthropic/openai/ollama_local/ollama_cloud/openrouter)
  provider?: string | null;
}

export interface OsEngineSetting {
  // Engine-agnostic: any engine_id string from the catalog, or null for system default.
  default_engine: string | null;
  // Hermes model alias (legacy field — general model hint is handled via default_worker_model).
  hermes_model: "hermes-fast" | "hermes-balanced" | "hermes-capable" | "hermes-large" | null;
  valid_engines: string[];
  ollama_reachable: boolean;
  // Worker engine (delegation target)
  default_worker_engine: string | null;
  default_worker_model: string | null;
  valid_worker_engines: string[];
  // Per-engine model overrides (ADR-0119)
  engine_models: Record<string, EngineModelConfig>;
  // Delegation flag — true when web_chat.delegation_enabled is set
  delegation_enabled: boolean;
  // ADR-0181 — L34/L35 advisories raised when saving cloud-model assignments
  compliance_warnings?: string[];
}

export interface OsEngineHealth {
  ollama_reachable: boolean;
  model_count: number;
  base_url_hash: string;
}

export async function getOsEngineSetting(signal?: AbortSignal): Promise<OsEngineSetting> {
  return api<OsEngineSetting>("/settings/engine", { signal });
}

export async function setOsEngineSetting(
  body: {
    default_engine: string | null;
    hermes_model: string | null;
    default_worker_engine?: string | null;
    default_worker_model?: string | null;
    engine_models?: Record<string, EngineModelConfig> | null;
  },
  csrf: string,
): Promise<OsEngineSetting> {
  return api<OsEngineSetting>("/settings/engine", { method: "PUT", body, csrf });
}

export async function getOsEngineHealth(signal?: AbortSignal): Promise<OsEngineHealth> {
  return api<OsEngineHealth>("/settings/engine/health", { signal });
}

export interface EngineCatalogEntry {
  id: string;
  label: string;
  description: string;
  local: boolean;
  requires: string;
  model_placeholder: string;
  model_examples: string;
  model_aliases: string[];
  os_capable: boolean;
}

export async function getEngineCatalog(signal?: AbortSignal): Promise<EngineCatalogEntry[]> {
  return api<EngineCatalogEntry[]>("/settings/engine/catalog", { signal });
}

// ── Engine Capability Matrix (ADR-0069 M5) ────────────────────────

export interface EngineCapabilityEntry {
  capabilities: Record<string, unknown>;
  command_manifest: {
    mid_stream_inject: string | null;
    cancel: string | null;
    compact: string | null;
    native_commands: Record<string, { description: string; usage: string }>;
  } | null;
  eaos_gaps: string[];
}

export interface EngineCapabilityMatrix {
  engines: Record<string, EngineCapabilityEntry>;
  eaos_milestones: Record<string, string>;
}

export async function getEngineCapabilities(
  signal?: AbortSignal,
): Promise<EngineCapabilityMatrix> {
  return api<EngineCapabilityMatrix>("/settings/engine/capabilities", { signal });
}

// ── Engine Detection (ADR-0125) ────────────────────────────────────

export type CredentialSource = "subscription" | "env_var" | "config_file" | "vault" | "none" | "discovered" | null;

export interface EngineProbeResult {
  engine_id: string;
  installed: boolean;
  authenticated: boolean;
  /** null means the binary is not installed */
  credential_source: CredentialSource;
  version: string | null;
  /** non-empty only for hermes — list of pulled Ollama model names */
  models: string[];
  detail: string | null;
}

export interface EngineDetectionResponse {
  results: EngineProbeResult[];
  /** engine_id of the best ready engine, or null */
  recommended_engine: string | null;
  /** true when no engine is authenticated — offer Hermes bootstrap */
  needs_bootstrap: boolean;
  /** set on detection errors (graceful fallback) */
  error?: string;
}

export interface HermesBootstrapResult {
  model_selected: string;
  ram_gb: number;
  ollama_installed: boolean;
  model_pulled: boolean;
  error: string | null;
  engine_configured?: boolean;
  hermes_model?: string;
}

interface HermesBootstrapStatus {
  state: "idle" | "running" | "done" | "error";
  phase?: string;
  result?: HermesBootstrapResult;
}

export async function detectEngines(signal?: AbortSignal): Promise<EngineDetectionResponse> {
  return api<EngineDetectionResponse>("/settings/engine/detect", { signal });
}

export async function getHermesBootstrapStatus(): Promise<HermesBootstrapStatus> {
  return api<HermesBootstrapStatus>("/settings/engine/bootstrap/status", { timeoutMs: 15_000 });
}

/**
 * Bootstrap Hermes: pulling the model (~5 GB) takes minutes, so the server runs
 * it in a background thread. This starts the job, then polls the status endpoint
 * until it reaches a terminal state — short individual requests, no 30 s-timeout
 * abort on the long pull. `onPhase` (optional) receives live phase strings.
 */
export async function bootstrapHermes(
  csrf: string,
  onPhase?: (phase: string) => void,
): Promise<HermesBootstrapResult> {
  // Start (or attach to an in-flight job) — fast, short timeout.
  await api<HermesBootstrapStatus>("/settings/engine/bootstrap", {
    method: "POST",
    csrf,
    timeoutMs: 20_000,
  });

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  // Poll for up to ~25 min (480 × ~3 s) — generous for a 5 GB pull on a slow link.
  for (let i = 0; i < 500; i++) {
    await sleep(3_000);
    let s: HermesBootstrapStatus;
    try {
      s = await getHermesBootstrapStatus();
    } catch {
      continue; // transient network blip — keep polling, the pull runs server-side
    }
    if (s.phase) onPhase?.(s.phase);
    if (s.state === "done" || s.state === "error") {
      return (
        s.result ?? {
          model_selected: "",
          ram_gb: 0,
          ollama_installed: false,
          model_pulled: s.state === "done",
          error: s.state === "error" ? "Bootstrap failed" : null,
        }
      );
    }
  }
  return {
    model_selected: "",
    ram_gb: 0,
    ollama_installed: false,
    model_pulled: false,
    error: "Bootstrap timed out — the model may still be downloading; click Test in a few minutes.",
  };
}

// ── Claude Code Local Backend (ADR-0126) ──────────────────────────────

export interface ClaudeLocalSetting {
  enabled: boolean;
  base_url: string;
  sonnet_model: string;
  haiku_model: string;
  opus_model: string;
  ollama_reachable: boolean;
  available_models: string[];
}

export async function getClaudeLocalSetting(signal?: AbortSignal): Promise<ClaudeLocalSetting> {
  return api<ClaudeLocalSetting>("/settings/engine/claude-local", { signal });
}

export async function setClaudeLocalSetting(
  body: {
    enabled: boolean;
    base_url: string;
    sonnet_model: string;
    haiku_model: string;
    opus_model: string;
  },
  csrf: string,
): Promise<ClaudeLocalSetting> {
  return api<ClaudeLocalSetting>("/settings/engine/claude-local", { method: "PUT", body, csrf });
}

// ── Engine model registry (ADR-0119) ─────────────────────────────────

export interface EngineModelEntry {
  id: string;
  label: string;
  default: boolean;
}

export interface EngineProviderSupport {
  provider: string;
  native: boolean;
  note: string;
}

export interface EngineRegistryEntry {
  label: string;
  supports_os_turn: boolean;
  supports_worker_turn: boolean;
  supports_task_type_steering: boolean;
  os_models: EngineModelEntry[];
  worker_models: EngineModelEntry[];
  // ADR-0181 — providers this engine can drive
  supported_providers?: EngineProviderSupport[];
}

export async function getEngineModelRegistry(
  signal?: AbortSignal,
): Promise<Record<string, EngineRegistryEntry>> {
  return api<Record<string, EngineRegistryEntry>>("/settings/engine/registry", { signal });
}

// ── Model providers + live model fetch (ADR-0181) ─────────────────────

export interface ProviderSpec {
  label: string;
  base_url: string;
  model_source: string;   // static | ollama | openrouter
  credential_env: string; // env-var NAME only, never a secret value
  kind: string;           // local | cloud
}

export async function getEngineProviders(
  signal?: AbortSignal,
): Promise<Record<string, ProviderSpec>> {
  return api<Record<string, ProviderSpec>>("/settings/engine/providers", { signal });
}

export interface ProviderModelsResponse {
  provider: string;
  reachable: boolean;
  models: { id: string; label: string }[];
  count: number;
  error: string | null;
  note?: string;
}

export async function getProviderModels(
  provider: string,
  signal?: AbortSignal,
): Promise<ProviderModelsResponse> {
  return api<ProviderModelsResponse>(
    `/settings/engine/models?provider=${encodeURIComponent(provider)}`,
    { signal },
  );
}

// ── Per-chat engine preference (ADR-0067) ─────────────────────────

export interface PerChatEnginePref {
  chat_key: string;
  per_chat_engine: string | null;
  per_chat_model: string | null;
  tenant_default: string | null;
  effective_engine: string;
  source: "per_chat" | "tenant_default" | "system_default";
}

export async function getPerChatEngine(
  chatKey: string,
  signal?: AbortSignal,
): Promise<PerChatEnginePref> {
  return api<PerChatEnginePref>(`/settings/engine-pref/${encodeURIComponent(chatKey)}`, { signal });
}

export async function setPerChatEngine(
  chatKey: string,
  engine: string,
  model: string | null,
  csrf: string,
): Promise<PerChatEnginePref> {
  return api<PerChatEnginePref>(`/settings/engine-pref/${encodeURIComponent(chatKey)}`, {
    method: "PUT",
    body: { engine, model },
    csrf,
  });
}

export async function clearPerChatEngine(
  chatKey: string,
  csrf: string,
): Promise<PerChatEnginePref> {
  return api<PerChatEnginePref>(`/settings/engine-pref/${encodeURIComponent(chatKey)}`, {
    method: "DELETE",
    csrf,
  });
}


// ── ADR-0120: Engine auto-detection ─────────────────────────────────

export interface EngineProbe {
  engine_id: string;
  found: boolean;
  version: string;
  detail: string;
  locality: "local" | "us_cloud" | "eu_cloud";
  capabilities: string[];
}

export interface EngineProbeResult {
  engines: EngineProbe[];
  onboarding_complete: boolean;
}

export async function getEngineProbes(signal?: AbortSignal): Promise<EngineProbeResult> {
  return api("/setup/onboarding/detect", { signal });
}


// ── ADR-0123: Per-persona engine & model config ───────────────────────────────

export interface PersonaEngineConfig {
  engine: string | null;
  os_model: string | null;
  worker_model: string | null;
  engine_lock: boolean;
  available_engines: string[];
  available_os_models: string[];
  available_worker_models: string[];
  registry: Record<
    string,
    {
      label?: string;
      supports_os_turn?: boolean;
      supports_worker_turn?: boolean;
      os_models?: { id: string; label: string; default?: boolean }[];
      worker_models?: { id: string; label: string; default?: boolean }[];
    }
  >;
}

export async function getPersonaEngine(
  name: string,
  signal?: AbortSignal,
): Promise<PersonaEngineConfig> {
  return api<PersonaEngineConfig>(
    `/personas/${encodeURIComponent(name)}/engine`,
    { signal },
  );
}

export interface PersonaEngineUpdateRequest {
  engine: string | null;
  os_model: string | null;
  worker_model: string | null;
  engine_lock: boolean;
}

export async function setPersonaEngine(
  name: string,
  cfg: PersonaEngineUpdateRequest,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>(
    `/personas/${encodeURIComponent(name)}/engine`,
    { method: "PUT", body: cfg, csrf },
  );
}
