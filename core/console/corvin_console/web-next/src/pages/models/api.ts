/**
 * Models-console API surface (ADR-0885). Every call goes through
 * `lib/api/client.ts::api()` — base `/v1/console`, JSON body, `X-CSRF-Token`
 * on writes, `ApiError` on non-2xx — so a mutation cannot forget the CSRF
 * header the way the old cost panel's bare `fetch()` did (every one of its
 * writes answered 403 on the live console, 2026-09-18).
 *
 * Bodies of the two window/reset calls are FIXED objects — never an
 * operator-typed string: `ResetRequest.reason` travelled into the audit
 * chain as free text before.
 */
import { api } from "@/lib/api/client";
import type { DashboardStatus } from "./components/cost-charts";

// ── Cost optimizer (ADR-0696/0760) ──────────────────────────────────────

export function getCostStatus(signal?: AbortSignal): Promise<DashboardStatus> {
  return api<DashboardStatus>("/learning/model-cost-optimizer/status", { signal });
}

export function postUsageEpoch(body: { clear?: true }, csrf: string): Promise<unknown> {
  return api("/learning/model-cost-optimizer/usage-epoch", { method: "POST", body, csrf });
}

export function postThresholdReset(csrf: string): Promise<unknown> {
  return api("/learning/model-cost-optimizer/reset", { method: "POST", body: {}, csrf });
}

export function getThresholdExport(signal?: AbortSignal): Promise<unknown> {
  return api<unknown>("/learning/model-cost-optimizer/export", { signal });
}

export function postThresholdImport(data: unknown, csrf: string): Promise<unknown> {
  return api("/learning/model-cost-optimizer/import", { method: "POST", body: { data }, csrf });
}

// ── Confidence learner analytics (ADR-0644/0885) ────────────────────────

export interface RankedRow {
  model: string;
  confidence: number;
  n_samples: number;
  mean_quality: number;
  variance: number;
  is_converged: boolean;
  posterior_mean: number | null;
  input_usd_per_1k: number | null;
  output_usd_per_1k: number | null;
  priced: boolean | null;
}

export interface TaskTypeRanking {
  task_type: string;
  timestamp: string;
  models: RankedRow[];
}

export function getTaskTypeRanking(taskType: string, signal?: AbortSignal): Promise<TaskTypeRanking> {
  return api<TaskTypeRanking>(`/v1/engine/analytics/task-type/${encodeURIComponent(taskType)}`, { signal });
}

export interface RecentClassification {
  record_hash: string;
  ts: number;
  task_type: string;
  model: string;
  confidence: number | null;
}

export interface RecentResponse {
  tenant_id: string;
  windowed: boolean;
  items: RecentClassification[];
}

export function getRecentClassifications(limit = 5, signal?: AbortSignal): Promise<RecentResponse> {
  return api<RecentResponse>(`/v1/engine/analytics/recent?limit=${limit}`, { signal });
}

export interface FeedbackResult {
  record_hash: string;
  task_type: string;
  model: string;
  confidence: number;
  n_samples: number;
  is_converged: boolean;
}

export function postFeedback(
  recordHash: string,
  rating: "good" | "poor",
  csrf: string,
): Promise<FeedbackResult> {
  return api<FeedbackResult>("/v1/engine/analytics/feedback", {
    method: "POST",
    body: { record_hash: recordHash, rating },
    csrf,
  });
}

export function postConfidenceReset(csrf: string): Promise<unknown> {
  return api("/v1/engine/analytics/reset", { method: "POST", body: {}, csrf });
}

// ── Model catalogue with published rates (ADR-0856) ─────────────────────

export interface CatalogModel {
  id: string;
  name: string;
  engines: string[];
  turns: string[];
  input_usd_per_1k: number | null;
  output_usd_per_1k: number | null;
  priced: boolean;
}

export interface CatalogResponse {
  models?: CatalogModel[];
  available?: boolean;
  detail?: string;
  unpriced?: string[];
}

export function getCatalog(signal?: AbortSignal): Promise<CatalogResponse> {
  return api<CatalogResponse>("/v1/models/available", { signal });
}
