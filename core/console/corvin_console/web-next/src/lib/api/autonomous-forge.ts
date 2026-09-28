/**
 * Autonomous Skill Forge — skill canary API (backend: routes/autonomous_forge_routes.py).
 */
import { api } from "./client";

const BASE = "/autonomous-forge";

export type CanaryStatus = "canary" | "paused" | "ready" | "approved" | "deferred" | "rolled_back";
export type Variant = "live" | "candidate";

export interface VariantStats {
  outcome_n: number;
  outcome_mean: number | null;
  usage_n: number;
  served_n: number;
}

export interface CanaryVerdict {
  decision: "hold" | "escalate" | "ready" | "rollback";
  reason: string;
}

export interface CanaryEvent {
  ts: number;
  type: string;
  hash: string;
  [key: string]: unknown;
}

export interface CanaryView {
  skill_id: string;
  canary_id: string;
  status: CanaryStatus;
  traffic_percent: number;
  source: "operator" | "autopilot" | string;
  trigger: { reason?: string; live_mean?: number | null; live_n?: number | null };
  quality: number | null;
  findings: { dimension: string; summary: string; verdict: string }[];
  created_at: number;
  updated_at: number;
  stats: Record<Variant, VariantStats>;
  reference: { mean: number | null; source: string };
  verdict: CanaryVerdict;
  gates: { min_samples: number; rollback_margin: number; traffic_steps: number[] };
  events?: CanaryEvent[];
}

export interface SkillRow {
  skill_id: string;
  description: string;
  outcome_n: number;
  outcome_mean: number | null;
  usage_n: number;
  loss_signal: boolean;
  canary_status: CanaryStatus | null;
}

export interface ForkRun {
  run_id: string;
  skill_id: string;
  status: "running" | "success" | "failed";
  phase: string;
  progress: number;
  message: string;
  error?: string | null;
}

export interface ForgeStatus {
  autopilot: {
    enabled: boolean;
    last_tick: number | null;
    last_actions: { skill_id: string; action: string }[];
    interval_s: number;
    loss_rule: { threshold: number; min_outcomes: number; window_days: number };
  };
  canaries: CanaryView[];
  skills: SkillRow[];
  forks_in_flight: string[];
  fork_runs: ForkRun[];
}

export interface MetricPoint {
  ts: number;
  variant: Variant;
  kind: "outcome" | "usage";
  score: number;
  outcome_mean: number | null;
  outcome_n: number;
}

export const getForgeStatus = () => api<ForgeStatus>(`${BASE}/status`);

export const getCanaryMetrics = (skillId: string) =>
  api<{ skill_id: string; points: MetricPoint[]; stats: Record<Variant, VariantStats> }>(
    `${BASE}/metrics?skill_id=${encodeURIComponent(skillId)}`,
  );

export const getCanaryHistory = (limit = 20) =>
  api<{ attempts: CanaryView[]; total_count: number }>(`${BASE}/history?limit=${limit}`);

export const getCandidate = (skillId: string) =>
  api<{ live_body: string; candidate_body: string; status: CanaryStatus }>(
    `${BASE}/candidate/${encodeURIComponent(skillId)}`,
  );

export const forkCandidate = (skillId: string, instruction = "") =>
  api<{ run_id: string }>(`${BASE}/fork`, { method: "POST", body: { skill_id: skillId, instruction } });

export const getForkRun = (runId: string) => api<ForkRun>(`${BASE}/fork/${encodeURIComponent(runId)}`);

export type CanaryAction = "approve" | "defer" | "pause" | "resume" | "rollback";

export const canaryAction = (action: CanaryAction, skillId: string, reason = "") =>
  api<{ canary: CanaryView }>(`${BASE}/${action}`, {
    method: "POST",
    body: { skill_id: skillId, reason },
  });

export const setAutopilot = (enabled: boolean) =>
  api<{ enabled: boolean }>(`${BASE}/autopilot`, { method: "POST", body: { enabled } });

export const runAutopilotTick = () => api<{ status: string }>(`${BASE}/tick`, { method: "POST", body: {} });
