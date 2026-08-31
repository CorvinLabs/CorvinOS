/**
 * api/compute — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── Compute Layer (Layer 25) ────────────────────────────────────────

export interface ComputeSystemResources {
  ram: { total_gb: number; used_gb: number; free_gb: number; used_pct: number } | null;
  cpu: { used_pct: number; core_count: number } | null;
  disk: { total_gb: number; free_gb: number; used_pct: number } | null;
}

export interface ComputeStatus {
  tenant_id: string;
  ts: number;
  enabled: boolean;
  worker_socket: { exists: boolean; reachable: boolean; error: string | null };
  run_count: number;
  runs: ComputeRun[];
  pipeline_count: number;
  hac_count: number;
  system: ComputeSystemResources;
}

export interface ComputeRun {
  run_id: string;
  tool_name: string | null;
  strategy: string | null;
  state: string | null;
  best_iter: number | null;
  best_loss: number | null;
  iterations: number;
  started_at: number | null;
  convergence: string | null;
  submitted_by: string | null;
  session_id: string | null;
  session_label: string | null;
}

export interface ComputeConfig {
  enabled: boolean;
  fabric_enabled: boolean;
  max_parallel_runs: number;
  run_ttl_days: number;
  yaml_exists: boolean;
}

export async function getComputeStatus(signal?: AbortSignal): Promise<ComputeStatus> {
  return api<ComputeStatus>("/compute", { signal });
}

// ADR-0099 — Anthropic Batch Compute open job list
export interface OpenBatchJob {
  job_id: string;
  batch_id_prefix: string;
  session_key: string;
  submitted_at: number | null;
  candidate_count: number | null;
  state: string;
  partial?: boolean;
  failed_candidate_count?: number;
}

export interface OpenBatchJobsResponse {
  tenant_id: string;
  open_count: number;
  jobs: OpenBatchJob[];
}

export async function getOpenBatchJobs(signal?: AbortSignal): Promise<OpenBatchJobsResponse> {
  return api<OpenBatchJobsResponse>("/compute/batch/open", { signal });
}

export async function getComputeConfig(signal?: AbortSignal): Promise<ComputeConfig> {
  return api<ComputeConfig>("/compute/config", { signal });
}

export async function updateComputeConfig(
  config: { enabled: boolean; fabric_enabled?: boolean; max_parallel_runs?: number; run_ttl_days?: number },
  csrf: string,
): Promise<{ ok: true; enabled: boolean }> {
  return api("/compute/config", { method: "PUT", csrf, body: { ...config } });
}

export async function submitComputeRun(
  // `params` is the hyperparameter SEARCH SPACE (grid/random/bayesian), so each
  // key maps to the list of candidate values — e.g. { w: [0, 0.5, 1] } — not a
  // single fixed value; the worker calls it param_grid internally.
  body: { tool_name: string; strategy: string; budget: Record<string, unknown>; objective: string; params?: Record<string, unknown> },
  csrf: string,
): Promise<{ ok: true; run_id: string; state: string }> {
  return api("/compute/runs", { method: "POST", csrf, body: { ...body } });
}

export async function deleteComputeRun(run_id: string, csrf: string): Promise<{ ok: true }> {
  return api(`/compute/runs/${encodeURIComponent(run_id)}`, { method: "DELETE", csrf });
}

export async function openRunDir(run_id: string, csrf: string): Promise<{ ok: true; path: string; launched: boolean }> {
  return api(`/compute/runs/${encodeURIComponent(run_id)}/open-dir`, { method: "POST", csrf });
}
export async function openPipelineDir(pipeline_id: string, csrf: string): Promise<{ ok: true; path: string; launched: boolean }> {
  return api(`/compute/pipelines/${encodeURIComponent(pipeline_id)}/open-dir`, { method: "POST", csrf });
}
export async function openHacDir(hac_id: string, csrf: string): Promise<{ ok: true; path: string; launched: boolean }> {
  return api(`/compute/hac/${encodeURIComponent(hac_id)}/open-dir`, { method: "POST", csrf });
}
export async function openAcsRunDir(run_id: string, csrf: string): Promise<{ ok: true; path: string; launched: boolean }> {
  return api(`/compute/acs/${encodeURIComponent(run_id)}/open-dir`, { method: "POST", csrf });
}

export interface ComputeIteration {
  iter: number;
  loss: number;
  params: Record<string, unknown>;
}

export interface ComputeRunDetail {
  run_id: string;
  manifest: {
    tool_name?: string;
    strategy?: string;
    budget?: { max_iterations?: number; timeout_s?: number };
    objective?: string;
    params?: Record<string, unknown>;
    started_at?: number;
    submitted_by?: string;
  };
  summary: {
    state?: string;
    best_iter?: number;
    best_loss?: number;
    convergence_reason?: string;
  };
  iterations: ComputeIteration[];
}

export async function getComputeRunDetail(run_id: string, signal?: AbortSignal): Promise<ComputeRunDetail> {
  return api<ComputeRunDetail>(`/compute/runs/${encodeURIComponent(run_id)}`, { signal });
}

export interface ComputeNarrative {
  text: string;
  locale: string;
  lang: string;
  model: string;
  generated_at: number;
}

export async function getComputeNarrative(
  run_id: string,
  opts: { force?: boolean; locale?: string; signal?: AbortSignal } = {},
): Promise<ComputeNarrative> {
  const params = new URLSearchParams();
  if (opts.force) params.set("force", "true");
  if (opts.locale) params.set("locale", opts.locale);
  const qs = params.toString() ? `?${params}` : "";
  return api<ComputeNarrative>(`/compute/runs/${encodeURIComponent(run_id)}/narrative${qs}`, {
    signal: opts.signal,
  });
}

export function computeRunVoiceUrl(run_id: string, force = false): string {
  const base = `/v1/console/compute/runs/${encodeURIComponent(run_id)}/voice`;
  return force ? `${base}?force=true` : base;
}

// ── Compute graph ────────────────────────────────────────────────────

export interface VisNode {
  id: string;
  label: string;
  shape: string;
  color: string | { background: string; border: string; highlight?: { background: string; border: string } };
  size: number;
  level: number;
  group: string;
  borderWidth?: number;
  font?: { color: string; size: number; face: string };
  title?: string;
  // Server returns additional runtime data fields beyond the VisJS display properties
  [key: string]: unknown;
}

export interface VisEdge {
  from: string;
  to: string;
  color: string;
  width: number;
  dashes?: boolean;
  label?: string;
  font?: { color: string; size: number; face: string };
}

export interface L25GraphPayload {
  mode: "l25";
  strategy: string;
  nodes: VisNode[];
  edges: VisEdge[];
  meta: {
    loss_min: number;
    loss_max: number;
    best_iter: number | null;
    n_iters: number;
    state: string;
  };
}

export interface ACSGraphPayload {
  mode: "acs";
  nodes: VisNode[];
  edges: VisEdge[];
  meta: {
    n_iters: number;
    n_workers: number;
    state: string;
    wall_time_s: number;
    quality_score: number | null;
  };
}

export async function getComputeRunGraph(
  run_id: string,
  opts: { signal?: AbortSignal } = {},
): Promise<L25GraphPayload> {
  return api<L25GraphPayload>(
    `/compute/runs/${encodeURIComponent(run_id)}/graph`,
    { signal: opts.signal },
  );
}

export async function getACSRunGraph(
  run_id: string,
  opts: { signal?: AbortSignal } = {},
): Promise<ACSGraphPayload> {
  return api<ACSGraphPayload>(
    `/compute/acs/${encodeURIComponent(run_id)}/graph`,
    { signal: opts.signal },
  );
}

// ADR-0214: TDE delegation audit graph — reconstructed from the hash-chained
// tde.* audit trail for one turn (see routes/compute.py::_build_tde_audit_graph).
// Unlike L25/ACS (built from manifest.json + artifact files on disk), this
// payload also carries chain-integrity verification for the turn's event span.
export interface TDEGraphPayload {
  mode: "tde";
  run_id: string;
  nodes: VisNode[];
  edges: VisEdge[];
  meta: {
    run_id: string;
    n_events: number;
    n_steps: number;
    n_delegated: number;
    n_local: number;
    wall_time_s: number | null;
    engine: string | null;
    confidence: number | null;
    loss_min: number | null;
    loss_max: number | null;
    loss_curve: { step: number | null; loss: number }[];
    chain_verified: boolean;
    chain_problems: Record<string, unknown>[];
  };
}

export async function getTdeRunGraph(
  run_id: string,
  opts: { signal?: AbortSignal } = {},
): Promise<TDEGraphPayload> {
  return api<TDEGraphPayload>(
    `/compute/tde/${encodeURIComponent(run_id)}/graph`,
    { signal: opts.signal },
  );
}

// ── Compute license / quota ─────────────────────────────────────────

export interface ComputeQuotaBucket {
  cap: number;
  used: number;
  remaining: number;
  pct_used: number;
}

export interface ComputeLicenseStatus {
  mode: "trial" | "licensed" | "grace" | "denied" | "unknown";
  tier: string;
  fabric_allowed: boolean;
  reason: string | null;
  upgrade_url: string;
  runs_today: number;
  daily_limit: number | null;   // null = unlimited; from compute_units_per_day
  quota: {
    grid_random: ComputeQuotaBucket;
    bayesian: ComputeQuotaBucket;
    first_run_at: number | null;
  } | null;
  license_meta: {
    customer_id_hint: string;
    expires_at: number | null;
    issued_at: number | null;
    feature_flags: string[];
  } | null;
}

export async function getComputeLicense(signal?: AbortSignal): Promise<ComputeLicenseStatus> {
  return api<ComputeLicenseStatus>("/compute/license", { signal });
}

// ── Pipeline types ──────────────────────────────────────────────────

export interface PipelineSummary {
  pipeline_id: string;
  name: string;
  stages: string[];
  stage_count: number;
  state: string | null;
  current_stage_id: string | null;
  completed_stages: string[];
  best_losses: Record<string, number>;
  started_at: number | null;
  submitted_by: string | null;
  steering_gate: boolean;
}

export interface PipelineStageDetail {
  stage_id: string;
  tool_name: string;
  strategy: string;
  state: string | null;
  best_loss: number | null;
  iter_count: number;
  iterations: { iter: number; loss: number }[];
  real_stats?: Record<string, unknown>;
}

export interface PipelineDetail {
  pipeline_id: string;
  manifest: {
    name?: string;
    stages?: unknown[];
    steering_gate?: boolean;
    started_at?: number;
    submitted_by?: string;
    budget?: Record<string, unknown>;
  };
  summary: {
    state?: string;
    current_stage_id?: string | null;
    completed_stages?: string[];
    best_losses?: Record<string, number>;
  };
  stages: PipelineStageDetail[];
}

export async function listPipelines(signal?: AbortSignal): Promise<{ pipeline_count: number; pipelines: PipelineSummary[] }> {
  return api("/compute/pipelines", { signal });
}

export async function getPipelineDetail(pipeline_id: string, signal?: AbortSignal): Promise<PipelineDetail> {
  return api<PipelineDetail>(`/compute/pipelines/${encodeURIComponent(pipeline_id)}`, { signal });
}

// ── HAC types ───────────────────────────────────────────────────────

export interface HacSummary {
  hac_id: string;
  name: string;
  state: string | null;
  round: number;
  max_rounds: number;
  root_loss: number | null;
  manager_count: number;
  aggregation_mode: string | null;
  fluid_reallocation: boolean;
  started_at: number | null;
  submitted_by: string | null;
  attributions: Record<string, number>;
}

export interface HacManagerDetail {
  manager_id: string;
  label?: string;
  budget_fraction: number;
  strategy: string;
  stages: PipelineStageDetail[];
  summary: { state?: string; best_loss?: number; current_loss?: number };
}

export interface HacDetail {
  hac_id: string;
  manifest: {
    name?: string;
    sub_managers?: unknown[];
    loss_weights?: { mode?: string; weights?: Record<string, number> };
    fluid_reallocation?: boolean;
    max_backprop_rounds?: number;
    backprop_gate?: boolean;
    started_at?: number;
    submitted_by?: string;
  };
  summary: {
    state?: string;
    round?: number;
    root_loss?: number | null;
    manager_states?: Record<string, unknown>;
    attributions?: Record<string, number>;
  };
  managers: HacManagerDetail[];
  loss_history: number[];
}

export async function listHacRuns(signal?: AbortSignal): Promise<{ hac_count: number; runs: HacSummary[] }> {
  return api("/compute/hac", { signal });
}

export async function getHacDetail(hac_id: string, signal?: AbortSignal): Promise<HacDetail> {
  return api<HacDetail>(`/compute/hac/${encodeURIComponent(hac_id)}`, { signal });
}


// ── Compute Settings ─────────────────────────────────────────────────

export interface ComputeSettings {
  default_strategy: "bayesian" | "grid" | "random";
  default_max_iterations: number;
  default_timeout_s: number;
  convergence_threshold: number | null;
  auto_champion: boolean;
  default_group_by: "none" | "session" | "tool" | "source" | "day" | "strategy";
  artifact_preview_rows: number;
  alert_loss_threshold: number | null;
  show_corpus_banner: boolean;
}

export async function getComputeSettings(signal?: AbortSignal): Promise<{ settings: ComputeSettings }> {
  return api("/compute/settings", { signal });
}

export async function updateComputeSettings(settings: ComputeSettings, csrf: string): Promise<{ ok: true; settings: ComputeSettings }> {
  return api("/compute/settings", { method: "PUT", body: settings, csrf });
}


// ── ADR-0124 M3: Compute Job Creator ─────────────────────────────────────────

export interface ComputeJob {
  job_id: string;
  name: string;
  job_type: "grid" | "pipeline" | "batch";
  strategy: "grid" | "random" | "bayesian";
  parameters: Record<string, unknown>;
  dataset_path: string | null;
  max_trials: number;
  description: string;
  status: "queued" | "running" | "completed" | "failed";
  created_at: number;
  updated_at: number;
}

export interface ComputeJobListResponse {
  tenant_id: string;
  count: number;
  jobs: ComputeJob[];
}

export async function listComputeJobs(signal?: AbortSignal): Promise<ComputeJobListResponse> {
  return api<ComputeJobListResponse>("/compute/jobs", { signal });
}

export interface ComputeJobSubmitRequest {
  name: string;
  job_type?: string;
  strategy?: string;
  parameters?: Record<string, unknown>;
  dataset_path?: string | null;
  max_trials?: number;
  description?: string;
}

export async function submitComputeJob(
  body: ComputeJobSubmitRequest,
  csrf: string,
): Promise<{ ok: boolean; job_id: string; status: string }> {
  return api("/compute/jobs", { method: "POST", body, csrf });
}

export async function cancelComputeJob(
  job_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/compute/jobs/${encodeURIComponent(job_id)}`, {
    method: "DELETE",
    csrf,
  });
}
