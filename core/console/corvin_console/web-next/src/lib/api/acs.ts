/**
 * api/acs — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── ACS Engine (ADR-0104) ─────────────────────────────────────────────────

export interface AcsManifest {
  run_id: string;
  workflow_id: string;
  status: "success" | "failed" | "budget_exhausted" | string;
  engine: string;
  started_at: number;
  completed_at: number;
  duration_s: number;
  iterations: number;
  workers_spawned: number;
  budget_breach: string;
  max_loops?: number;
  max_workers_per_iteration?: number;
  max_wall_time?: number;
}

export interface AcsRunResult {
  run_id: string;
  workflow_id: string;
  status: string;
  summary: string;
  final_output: Record<string, unknown>;
  error: string;
  iterations: number;
  workers_spawned: number;
  budget_breach: string;
  elapsed_s: number;
}

export interface AcsIteration {
  iteration: number;
  decision: "DELEGATE" | "COMPLETE" | "FAIL" | string;
  reasoning_len: number;
}

export interface AcsGateEntry {
  gate_id: string;
  passed: boolean;
  score: number;
  reason: string;
}

export interface AcsLossDimensions {
  completeness: number;
  novelty: number;
  quality: number;
  metrics: number;
  confidence: number;
}

export interface AcsWorkerAttribution {
  worker_id: string;
  status: string;
  confidence: number;
  attribution: number;
}

export interface AcsGateResult {
  iteration: number;
  passed: boolean;
  aggregate_score: number;
  gates: AcsGateEntry[];
  loss_total?: number;
  loss_delta?: number | null;
  loss_dimensions?: AcsLossDimensions;
  worker_attributions?: AcsWorkerAttribution[];
}

export interface AcsWorkerResult {
  worker_id: string;
  status: "success" | "partial" | "failed" | string;
  confidence: number;
  iteration: number;
  depth: number;
}

export interface AcsRunDetail {
  manifest: AcsManifest;
  result: AcsRunResult;
  iterations: AcsIteration[];
  gate_results: AcsGateResult[];
  workers: AcsWorkerResult[];
  graph_exportable?: boolean;
}

export interface AcsRunsResponse {
  engine: string;
  available: boolean;
  run_count: number;
  runs: AcsManifest[];
}

export async function listAcsRuns(signal?: AbortSignal): Promise<AcsRunsResponse> {
  return api<AcsRunsResponse>("/compute/acs", { signal });
}

export async function getAcsRun(runId: string, signal?: AbortSignal): Promise<AcsRunDetail> {
  return api<AcsRunDetail>(`/compute/acs/${encodeURIComponent(runId)}`, { signal });
}

export async function exportAcsRun(
  runId: string,
  mode: "dag" | "template",
  description: string,
  csrf: string,
): Promise<Blob> {
  const base = (window as Window & { __CORVIN_API_BASE__?: string }).__CORVIN_API_BASE__ ?? "/v1/console";
  const resp = await fetch(`${base}/compute/acs/${encodeURIComponent(runId)}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    credentials: "include",
    body: JSON.stringify({ mode, description }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`Export failed (${resp.status}): ${text}`);
  }
  return resp.blob();
}
