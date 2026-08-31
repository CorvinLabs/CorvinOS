/**
 * api/data — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { ApiError, BASE, api } from "./client";

// ── ECIL: Corpus Context (M1) ───────────────────────────────────────

export interface CorpusContext {
  pipeline_name: string | null;
  has_corpus: boolean;
  real_stats: {
    total_rows?: number;
    output_rows?: number;
    unique_countries?: number;
    iso_weeks?: number;
    file_size_mb?: number;
    compression_factor?: number;
    watermark_date?: string;
    date_range_start?: string;
    date_range_end?: string;
    pii_detected?: boolean;
    zone?: string;
    top_tracks?: { track_name: string; artist: string; total_streams: number; peak_rank: number; days_on_chart: number }[];
    column_stats?: Record<string, { unique?: number; min?: number; max?: number; p50?: number; p95?: number; p99?: number }>;
    schema?: { name: string; type: string; nullable?: boolean }[];
  };
}

export async function getCorpusContext(signal?: AbortSignal): Promise<CorpusContext> {
  return api<CorpusContext>("/compute/corpus-context", { signal });
}

// ── ECIL: Experiments (M2) ──────────────────────────────────────────

export interface Experiment {
  experiment_id: string;
  name: string;
  hypothesis: string;
  session_id: string | null;
  session_label: string | null;
  baseline_run_id: string | null;
  champion_run_id: string | null;
  run_ids: string[];
  tags: string[];
  locked: boolean;
  created_at: number;
}

export interface ExperimentRunDetail {
  run_id: string;
  tool_name: string | null;
  strategy: string | null;
  params: Record<string, unknown>;
  best_loss: number | null;
  best_iter: number | null;
  convergence: string | null;
  state: string | null;
  iterations_done: number;
  budget_max: number | null;
  submitted_by: string | null;
  session_label: string | null;
  started_at: number | null;
  is_baseline: boolean;
  is_champion: boolean;
}

export interface ExperimentDetail extends Experiment {
  runs_detail: ExperimentRunDetail[];
}

export async function listExperiments(signal?: AbortSignal): Promise<{ count: number; experiments: Experiment[] }> {
  return api("/compute/experiments", { signal });
}

export async function getExperimentDetail(id: string, signal?: AbortSignal): Promise<ExperimentDetail> {
  return api<ExperimentDetail>(`/compute/experiments/${encodeURIComponent(id)}`, { signal });
}

// ── ECIL: Artifact Viewer (M4) ──────────────────────────────────────

export interface ArtifactStats {
  stage_id: string;
  state: string | null;
  real_stats: CorpusContext["real_stats"];
  artifacts: { filename: string; size_bytes: number; size_mb: number; extension: string }[];
  pii_columns: string[];
}

// ── DataTable types (shared between Compute + Workflow layers) ─────────────

export interface TablePageResponse {
  filename?: string;
  rows_returned: number;
  schema: { name: string; type: string }[];
  rows: Record<string, unknown>[];
  total_rows: number;
  page: number;
  per_page: number;
  total_pages: number;
  sort_col: string | null;
  sort_dir: "asc" | "desc";
  filter_text: string;
  pii_redacted: string[];
  all_columns?: string[];
}

// Legacy alias
export type ArtifactPreview = TablePageResponse;

export interface TableQueryParams {
  page?: number;
  per_page?: number;
  sort_col?: string | null;
  sort_dir?: "asc" | "desc";
  filter?: string;
  cols?: string;
}

function buildTableQuery(filename: string, params: TableQueryParams = {}): string {
  const q = new URLSearchParams({ filename: filename });
  if (params.page != null) q.set("page", String(params.page));
  if (params.per_page != null) q.set("per_page", String(params.per_page));
  if (params.sort_col) q.set("sort_col", params.sort_col);
  if (params.sort_dir) q.set("sort_dir", params.sort_dir);
  if (params.filter) q.set("filter", params.filter);
  if (params.cols) q.set("cols", params.cols);
  return q.toString();
}

export async function getArtifactStats(pipelineId: string, stageId: string, signal?: AbortSignal): Promise<ArtifactStats> {
  return api<ArtifactStats>(`/compute/pipelines/${encodeURIComponent(pipelineId)}/stages/${encodeURIComponent(stageId)}/artifact-stats`, { signal });
}

export async function getArtifactPreview(
  pipelineId: string,
  stageId: string,
  filename: string,
  rows = 50,
  signal?: AbortSignal,
  params: TableQueryParams = {},
): Promise<TablePageResponse> {
  const q = buildTableQuery(filename, { per_page: rows, ...params });
  return api<TablePageResponse>(
    `/compute/pipelines/${encodeURIComponent(pipelineId)}/stages/${encodeURIComponent(stageId)}/artifact-preview?${q}`,
    { signal },
  );
}

export function artifactDownloadUrl(pipelineId: string, stageId: string, filename: string): string {
  return `/v1/console/compute/pipelines/${encodeURIComponent(pipelineId)}/stages/${encodeURIComponent(stageId)}/artifact-download?filename=${encodeURIComponent(filename)}`;
}

// ── Workflow Run Table API ──────────────────────────────────────────────────

export interface WorkflowTableItem {
  filename: string;
  mime_type: string;
  size_bytes: number;
  row_count?: number | null;
  src: string;
  ts: number;
}

export async function getWorkflowRunTables(
  wid: string,
  rid: string,
  signal?: AbortSignal,
): Promise<{ run_id: string; tables: WorkflowTableItem[]; count: number }> {
  return api(`/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}/tables`, { signal });
}

export async function getWorkflowRunTablePage(
  wid: string,
  rid: string,
  filename: string,
  params: TableQueryParams = {},
  signal?: AbortSignal,
): Promise<TablePageResponse> {
  const q = buildTableQuery(filename, params);
  return api<TablePageResponse>(
    `/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}/tables/${encodeURIComponent(filename)}?${q}`,
    { signal },
  );
}

export function workflowTableZipUrl(wid: string, rid: string): string {
  return `/v1/console/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}/tables.zip`;
}

export function experimentJupyterUrl(experimentId: string): string {
  return `/v1/console/compute/experiments/${encodeURIComponent(experimentId)}/export/jupyter`;
}

export function experimentMlflowUrl(experimentId: string): string {
  return `/v1/console/compute/experiments/${encodeURIComponent(experimentId)}/export/mlflow`;
}

export function experimentReportUrl(experimentId: string): string {
  return `/v1/console/compute/experiments/${encodeURIComponent(experimentId)}/report`;
}


// ── ADR-0090: Pipeline → awpkg export ──────────────────────────────────

export interface AwpkgDatasourceInfo {
  name: string;
  adapter: string;
  region: string;
  classification_inferred: string;
  has_watermark: boolean;
  secret_key_count: number;
}

export interface AwpkgPreview {
  pipeline_id: string;
  stage_count: number;
  tool_names: string[];
  dag_nodes: number;
  rag_providers: { provider_id: string; classification: string; zone: string }[];
  fabric_datasources: AwpkgDatasourceInfo[];
  output_datasources: AwpkgDatasourceInfo[];
  ml_backend_count: number;
  custom_adapter_count: number;
  acceptance_criteria_stages: string[];
  schedule_detected: string | null;
  secrets_required: string[];
  estimated_size_kb: number;
  mode_options: string[];
}

export interface AwpkgExportRequest {
  package_id: string;
  version: string;
  mode: "replay" | "reoptimize";
  include_sample_data: boolean;
  sample_rows: number;
  include_rag_manifests: boolean;
  include_fabric_datasources: boolean;
  include_output_datasources: boolean;
  include_watermarks: boolean;
  include_custom_adapters: boolean;
  include_ml_backends: boolean;
  schedule_cron: string | null;
  schedule_timezone: string;
  acceptance_criteria: { max_best_loss?: number; min_improvement_pct?: number; on_fail?: string } | null;
}

export async function getAwpkgPreview(
  pipeline_id: string,
  signal?: AbortSignal,
): Promise<AwpkgPreview> {
  return api<AwpkgPreview>(
    `/compute/pipelines/${encodeURIComponent(pipeline_id)}/export/awpkg/preview`,
    { signal },
  );
}

/** Triggers a ZIP download — returns the Response so the caller can handle the blob. */
export async function downloadAwpkg(
  pipeline_id: string,
  body: AwpkgExportRequest,
  csrf: string,
): Promise<Response> {
  const res = await fetch(
    `${BASE}/compute/pipelines/${encodeURIComponent(pipeline_id)}/export/awpkg`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
      },
      credentials: "include",
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || res.statusText);
  }
  return res;
}

export interface PromoteChampionRequest {
  run_id: string;
  package_id: string;
  current_version: string;
  improvement_threshold_pct: number;
}

export interface PromoteChampionResult {
  promoted: boolean;
  run_id: string;
  new_version: string;
  new_best_loss: number;
  current_best_loss: number | null;
  improvement_pct: number | null;
  reason?: string;
  next_step?: string;
}

export async function promoteChampion(
  pipeline_id: string,
  body: PromoteChampionRequest,
  csrf: string,
): Promise<PromoteChampionResult> {
  return api<PromoteChampionResult>(
    `/compute/pipelines/${encodeURIComponent(pipeline_id)}/promote-champion`,
    { method: "POST", body, csrf },
  );
}

export async function pipelineToWorkflow(
  pipeline_id: string,
  body: AwpkgExportRequest,
  csrf: string,
): Promise<{ ok: true; workflow_id: string; workflow_name: string; redirect_url: string }> {
  return api(
    `/compute/pipelines/${encodeURIComponent(pipeline_id)}/export/awpkg/to-workflow`,
    { method: "POST", body, csrf },
  );
}

// ── RAG Integration (Phase 4) ───────────────────────────────────────

export interface RAGProvider {
  id: string;
  name: string;
  status: "active" | "inactive";
  health_status: "healthy" | "unhealthy" | "unknown";
  latency_ms: number;
  query_stats: {
    total_queries: number;
    queries_today: number;
    average_latency_ms: number;
  };
}

export interface RAGQueryRequest {
  query: string;
  limit?: number;
  preferred_providers?: string[];
  timeout_ms?: number;
}

export interface RAGResultItem {
  content: string;
  score: number;
  metadata: Record<string, unknown>;
  source_url?: string;
}

export interface RAGQueryResponse {
  items: RAGResultItem[];
  total_time_ms: number;
  providers_queried: number;
  cache_hit: boolean;
}

export async function listRAGProviders(
  signal?: AbortSignal,
): Promise<{ providers: RAGProvider[]; registered_count: number }> {
  return api<{ providers: RAGProvider[]; registered_count: number }>("/rag/providers", { signal });
}

export async function getRAGProviderHealth(providerId: string, signal?: AbortSignal): Promise<RAGProvider> {
  return api<RAGProvider>(`/rag/providers/${encodeURIComponent(providerId)}/health`, { signal });
}

export async function executeRAGQuery(req: RAGQueryRequest, csrf?: string, signal?: AbortSignal): Promise<RAGQueryResponse> {
  return api<RAGQueryResponse>("/rag/query", { method: "POST", body: req, csrf, signal });
}


// ── Media (ADR-0088 M7 + ADR-0091) ────────────────────────────────────────

export interface MediaItem {
  media_id: string;
  node_id?: string;
  stage_id?: string;
  pipeline_id?: string;
  filename: string;
  mime_type: string;
  label: string | null;
  size_bytes?: number;
  src: string;
  thumbnail_src: string | null;
  width?: number;
  height?: number;
  ts: number;
}

// ADR-0091: workflow run media
export async function getWorkflowRunMedia(
  wid: string, rid: string, signal?: AbortSignal
): Promise<{ run_id: string; media: MediaItem[] }> {
  return api(`/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}/media`, { signal });
}

export function workflowMediaUrl(wid: string, rid: string, filename: string): string {
  return `${BASE}/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}/media/${encodeURIComponent(filename)}`;
}

export function workflowMediaZipUrl(wid: string, rid: string): string {
  return `${BASE}/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}/media.zip`;
}

// ADR-0088 M7: compute stage image
export function computeStageImageUrl(pipelineId: string, stageId: string, filename: string): string {
  return `${BASE}/compute/pipelines/${encodeURIComponent(pipelineId)}/stages/${encodeURIComponent(stageId)}/artifact-image/${encodeURIComponent(filename)}`;
}

// media_attachments on experiment (ADR-0088 M7)
export interface MediaAttachment {
  attachment_id: string;
  source: "compute_stage" | "workflow_run";
  pipeline_id?: string | null;
  stage_id?: string | null;
  wid?: string | null;
  run_id?: string | null;
  filename: string;
  label: string | null;
  mime_type: string;
  attached_at: number;
}
