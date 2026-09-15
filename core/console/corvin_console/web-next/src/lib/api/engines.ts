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

/** GET /settings/engine.
 *
 *  Kept in step with `routes/engine.py::EngineSettingResponse` — that model is
 *  the contract, and this interface had drifted past it. Six fields declared
 *  here as REQUIRED (`hermes_model`, `ollama_reachable`, `default_worker_engine`,
 *  `default_worker_model`, `delegation_enabled`, plus `valid_worker_engines`)
 *  are not returned by the route at all: a caller reading any of them got
 *  `undefined` while the compiler promised a value. They are marked optional
 *  below rather than deleted, because an older gateway on the other end of the
 *  same console build may still send them. */
export interface OsEngineSetting {
  // Engine-agnostic: any engine_id string from the catalog, or null for system default.
  default_engine: string | null;
  valid_engines: string[];
  // Per-engine model overrides (ADR-0119)
  engine_models: Record<string, EngineModelConfig>;
  // ADR-0181 — L34/L35 advisories raised when saving cloud-model assignments
  compliance_warnings?: string[];

  /** ── Not sent by the current backend. Optional, never assume present. ── */
  hermes_model?: "hermes-fast" | "hermes-balanced" | "hermes-capable" | "hermes-large" | null;
  ollama_reachable?: boolean;
  default_worker_engine?: string | null;
  default_worker_model?: string | null;
  valid_worker_engines?: string[];
  delegation_enabled?: boolean;
}

export interface OsEngineHealth {
  ollama_reachable: boolean;
  model_count: number;
  base_url_hash: string;
}

export async function getOsEngineSetting(signal?: AbortSignal): Promise<OsEngineSetting> {
  return api<OsEngineSetting>("/settings/engine", { signal });
}

/** PUT /settings/engine.
 *
 *  The body shape is `routes/engine.py::EngineSettingUpdate`, which is
 *  `extra="forbid"`: it accepts EXACTLY `default_engine` and `engine_models`.
 *  This signature previously required `hermes_model` and offered
 *  `default_worker_engine` / `default_worker_model`, none of which that model
 *  accepts — so any call built to satisfy the TypeScript signature was
 *  guaranteed to come back 422 `extra_forbidden`. Verified against the live
 *  route 2026-09-15. Widen the Pydantic model first if these need to return. */
export async function setOsEngineSetting(
  body: {
    default_engine: string | null;
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

export type CredentialSource =
  | "subscription" | "env_var" | "bedrock" | "vertex" | "foundry"
  | "config_file" | "vault" | "none" | "discovered" | null;

export interface EngineProbeResult {
  engine_id: string;
  installed: boolean;
  authenticated: boolean;
  /** null means the binary is not installed */
  credential_source: CredentialSource;
  version: string | null;
  /** non-empty only for hermes — list of pulled Ollama model names */
  models: string[];
  /** ADR-0759 — which plan backs an authenticated engine: "pro" | "max" |
   *  "team" | "enterprise" for an OAuth subscription, or the platform id
   *  ("bedrock" | "vertex" | "foundry"). "" when the host cannot say. */
  plan?: string;
  /** Vendor's own rate-limit tier label. Display only — never parsed. */
  rate_limit_tier?: string;
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

// ── ADR-0641/0642: Model Selection Config (per-task-type) ─────────────────

export type TaskType = "corvinOS" | "SIMPLE" | "MEDIUM" | "COMPLEX";

export interface TaskModelConfig {
  task_type: TaskType;
  selected_model: string;
  // ADR-0181 provider id; null = native Anthropic
  provider: string | null;
  alternatives: string[];
  // REAL, LEARNED (ADR-0644 Bayesian + EMA) — core.learning.
  // model_selection_optimizer.ConfidenceOptimizer, fed by real turn outcomes
  // (corvin_operator/bridges/shared/model_selector_shadow.py::report_turn_outcome).
  // 0 until real turns for this tier's current model have completed.
  confidence_score: number;
  run_count: number;
  // True only once the real variance criterion is met (n>=5, then <0.05
  // variance over the last 50 samples) — never claimed early.
  is_converged: boolean;
  // Real turns the shadow classifier assigned to THIS tier, counted from the
  // audit chain. Not the same as run_count: that one is outcome samples for
  // (tier, currently-selected model) and resets when the tier is re-pointed at
  // another model. This is what lets an empty tier say WHY it is empty —
  // "0 of 5 classified turns were MEDIUM" rather than a bare "nothing yet".
  classified_count: number;
}

export interface EngineConfigResponse {
  tenant_id: string;
  models: Record<TaskType, TaskModelConfig>;
  last_updated: string;
  learning_status: "idle" | "learning" | "converged";
  last_learning_update: string | null;
  // Real classification count (corvin_operator/bridges/shared/adapter.py's shadow
  // classify, every real turn) — independent of which model was selected.
  total_samples: number;
  // Real outcome-feedback samples across all tiers' currently selected models.
  total_learned_samples: number;
}

export async function getEngineConfig(signal?: AbortSignal): Promise<EngineConfigResponse> {
  return api<EngineConfigResponse>("/v1/engine/config", { signal });
}

export interface TaskModelConfigUpdate {
  task_type: TaskType;
  selected_model: string;
  provider: string | null;
  alternatives: string[];
}

export async function setEngineConfig(
  models: Partial<Record<TaskType, TaskModelConfigUpdate>>,
  csrf: string,
): Promise<EngineConfigResponse> {
  return api<EngineConfigResponse>("/v1/engine/config", {
    method: "PUT",
    body: { models },
    csrf,
  });
}

export interface ExternalProviderTestResult {
  is_connected: boolean;
  latency_ms: number | null;
  error_message: string | null;
  model_count: number;
}

export async function testExternalProvider(
  provider: string,
  csrf: string,
): Promise<ExternalProviderTestResult> {
  return api<ExternalProviderTestResult>("/v1/engine/external-provider/test", {
    method: "POST",
    body: { provider },
    csrf,
  });
}

// ── ADR-0644: Model Selection Analytics (real Bayesian confidence, currently
// empty until outcome/quality feedback is wired — core/learning/
// model_selection_optimizer.py::process_feedback has no production caller yet) ──

export interface ConfidenceEntry {
  model: string;
  confidence: number;
  n_samples: number;
  mean_quality: number;
  variance: number;
  is_converged: boolean;
}

export interface AnalyticsSummary {
  timestamp: string;
  tenant_id: string;
  total_samples: number;
  models: ConfidenceEntry[];
  top_model: string | null;
  top_confidence: number | null;
}

export async function getModelSelectionAnalytics(signal?: AbortSignal): Promise<AnalyticsSummary> {
  return api<AnalyticsSummary>("/v1/engine/analytics", { signal });
}

export async function resetModelSelectionLearning(
  csrf: string,
): Promise<{ status: string; message: string; reset_at: string }> {
  return api("/v1/engine/analytics/reset", { method: "POST", csrf });
}

// ── Claude model catalogue — union of every live source ────────────────────
// registry (ADR-0119 curated, offline) + anthropic_live (GET /v1/models) +
// bedrock_live (ListFoundationModels + ListInferenceProfiles, SigV4). No
// hardcoded model list in this file: on a Bedrock host the selectable ids are
// `us.anthropic.claude-*` inference profiles that no shipped list can predict.

export interface ClaudeModelEntry {
  id: string;
  label: string;
  /** which sources offered this id: "registry" | "anthropic_live" | "bedrock_live" */
  sources: string[];
  providers: string[];
}

export interface ClaudeModelSource {
  id: string;
  label: string;
  /** `label` without its trailing qualifier, for the compact one-line summary */
  short_label?: string | null;
  reachable: boolean;
  /** Claude models found by THIS source (not the provider's whole catalogue) */
  count: number;
  error: string | null;
  /** `error` cut to its first clause — the compact line's share of it. Never a
   *  replacement for `error`, which the panel keeps on hover. */
  hint?: string | null;
  live: boolean;
  /** e.g. Bedrock's resolved region + credential source */
  detail?: string | null;
  /** This source needs an API key and the host has none. NOT a malfunction: a
   *  Bedrock- or subscription-authenticated install never has an Anthropic key,
   *  so the source is unused here rather than down. The panel drops it from the
   *  compact line while another live source is answering. */
  credential_absent?: boolean;
}

export interface ClaudeModelsResponse {
  tenant_id: string;
  models: ClaudeModelEntry[];
  count: number;
  sources: ClaudeModelSource[];
  /** The ADR-0119 registry's `default: true` worker model, or null when the
   *  registry declares none. Used as the reset target when an external provider
   *  is removed — never a positional index into a frontend array. */
  default_model_id: string | null;
}

export async function getClaudeModels(signal?: AbortSignal): Promise<ClaudeModelsResponse> {
  return api<ClaudeModelsResponse>("/v1/engine/claude-models", { signal });
}

// ── Real model usage shares, counted from the tenant audit chain ───────────

export interface ModelUsageRow {
  model_id: string;
  provider: string;
  provider_label: string;
  /** how the provider was determined: live_catalog | registry | id_prefix | unresolved */
  provider_source: string;
  turns: number;
  share_pct: number;
  ok: number;
  failed: number;
  unfinished: number;
  success_pct: number;
  avg_duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  total_tokens: number;
  token_share_pct: number;
  roles: Record<string, number>;
  engines: string[];
  first_seen: number | null;
  last_seen: number | null;
}

export interface ProviderUsageRow {
  provider: string;
  provider_label: string;
  turns: number;
  total_tokens: number;
  models: number;
  share_pct: number;
  token_share_pct: number;
}

/** ADR-0759 — one row per engine ROLE (os | worker | manager). The OS turn and
 *  the worker turn it delegates to run different engines on different models;
 *  summed together they hide exactly the split an operator is looking for. */
export interface RoleUsageRow {
  role: string;
  turns: number;
  ok: number;
  failed: number;
  unfinished: number;
  success_pct: number;
  share_pct: number;
  avg_duration_ms: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  token_share_pct: number;
  models: string[];
  engines: string[];
  /** false = this role ran real turns but its emitter reported no token counts.
   *  Distinct from "cost nothing" and must be rendered as such. */
  tokens_reported: boolean;
}

export interface ModelUsageResponse {
  tenant_id: string;
  chain_path_resolved: boolean;
  /** false = the chain file does not exist yet (fresh install), not an error */
  chain_readable: boolean;
  models: ModelUsageRow[];
  providers: ProviderUsageRow[];
  roles: RoleUsageRow[];
  /** ADR-0760 counting window. {active:false} = all-time. Shipped alongside the
   *  numbers so a total can never be rendered without the period it covers. */
  window?: {
    active: boolean;
    epoch_ts: number | null;
    since_iso: string | null;
    reason: string;
  };
  totals: {
    turns: number;
    ok: number;
    failed: number;
    unfinished: number;
    total_tokens: number;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
  };
}

export async function getModelUsage(signal?: AbortSignal): Promise<ModelUsageResponse> {
  return api<ModelUsageResponse>("/v1/engine/model-usage", { signal });
}
