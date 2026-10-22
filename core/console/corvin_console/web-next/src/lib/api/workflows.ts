/**
 * api/workflows — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api, ApiError, BASE } from "./client";

// ── Workflow Builder (ADR-0039) ─────────────────────────────────────

export interface WorkflowMeta {
  id: string;
  title: string;
  description: string;
  phase: "discovering" | "structuring" | "detailing" | "ready";
  created_at: number;
  updated_at: number;
  has_schedule: boolean;
  schedule?: { cron: string; timezone: string; overrun: string } | null;
  /** ADR-0090: set to "compute_pipeline" when imported from a compute pipeline export */
  source?: string;
  /** ADR-0090: pipeline_id of the source pipeline when source === "compute_pipeline" */
  pipeline_id?: string;
}

export interface GraphNode {
  id: string;
  type: string;
  depends_on: string[];
  agent?: string | null;
  instructions: string;
  tools?: string[];
  config?: Record<string, unknown> | null;  // deliver node: {channel, chat_id, format}
  /** The node exactly as written in the YAML (every type's fields). */
  spec?: Record<string, unknown>;
}

export interface WorkflowNodeResponse {
  ok: true;
  id: string;
  yaml: string;
  graph: GraphNode[];
}

export interface ChatEntry {
  role: "user" | "assistant";
  content: string;
  ts: number;
  yaml_update?: string;
  phase_update?: string;
  summary_card?: Record<string, unknown>;
  template_offer?: { key: string; yaml: string; confidence: number };
  graph?: GraphNode[];
  /** Files sent with this user message (ids and display fields only). */
  attachments?: { id: string; name: string; size: number; mime: string }[];
  /** A gate-refused user line: its text was never stored (hash + length only). */
  refused?: boolean;
  /** Slash-command exchange: answered by the server, never part of the transcript. */
  command?: boolean;
}

export interface WorkflowListResponse {
  tenant_id: string;
  count: number;
  workflows: WorkflowMeta[];
}

export interface WorkflowDetailResponse {
  workflow: WorkflowMeta;
  yaml: string;
  graph: GraphNode[];
  chat: ChatEntry[];
}

export interface RunMeta {
  rid: string;
  wid: string;
  status: "running" | "complete" | "failed" | "paused";
  dry_run: boolean;
  started_at: number;
  finished_at: number | null;
  ok: boolean | null;
  error: string | null;
  /** Set while status is "paused": the question the run is waiting on. */
  awaiting?: {
    node_id: string;
    kind: "approval" | "ask_human";
    message: string;
    expires_at_ms: number | null;
  } | null;
}

export interface RunDetailResponse {
  run: RunMeta;
  events: WorkflowRunEvent[];
}

export interface WorkflowRunEvent {
  type: "node_started" | "node_completed" | "node_failed" | "node_skipped" | "node_awaiting_approval" | "node_awaiting_reply" | "run_completed" | "run_paused" | "error" | "media" | "table";
  ts: number;
  node_id?: string;
  tokens?: number;
  elapsed_s?: number;
  error?: string;
  message?: string;
  timeout_s?: number;
  ok?: boolean;
  dry_run?: boolean;
  /** Set on the end of a synthetic check (the former dry run): a stub run, not a real one. */
  synthetic?: boolean;
  ready?: boolean;
  budget?: Record<string, unknown>;
  output_preview?: string;
  output?: string;            // full node output (up to 50 KB)
  // ADR-0091 M3: media event fields
  media_id?: string;
  filename?: string;
  mime_type?: string;
  label?: string;
  src?: string;
  thumbnail_src?: string | null;
}

export async function approveRunNode(
  wid: string,
  rid: string,
  comment: string,
  csrf: string,
): Promise<{ ok: true; status: "approved" }> {
  return api(`/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}/approve`, {
    method: "POST",
    csrf,
    body: { comment },
  });
}

export async function rejectRunNode(
  wid: string,
  rid: string,
  comment: string,
  csrf: string,
): Promise<{ ok: true; status: "rejected" }> {
  return api(`/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}/reject`, {
    method: "POST",
    csrf,
    body: { comment },
  });
}

// ADR-0188 — resume a run paused at an `ask_human` node with a free-text
// (or yes/no) reply. `confirmed` is present only when the node declared an
// `expect: {type: boolean}` field; absent for plain free-text replies.
export async function resumeWorkflowRun(
  wid: string,
  rid: string,
  reply: string,
  csrf: string,
): Promise<{ ok: true; status: "complete" | "failed" | "paused"; confirmed?: boolean }> {
  return api(`/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}/resume`, {
    method: "POST",
    csrf,
    body: { reply },
  });
}

export async function listWorkflows(signal?: AbortSignal): Promise<WorkflowListResponse> {
  return api<WorkflowListResponse>("/workflows", { signal });
}

export async function getWorkflow(
  wid: string,
  signal?: AbortSignal,
): Promise<WorkflowDetailResponse> {
  return api<WorkflowDetailResponse>(`/workflows/${encodeURIComponent(wid)}`, { signal });
}

export async function createWorkflow(
  body: { id: string; title?: string; description?: string; yaml?: string },
  csrf: string,
): Promise<{ ok: true; workflow: WorkflowMeta }> {
  return api("/workflows", { method: "POST", csrf, body });
}

export async function patchWorkflow(
  wid: string,
  body: { title?: string; description?: string },
  csrf: string,
): Promise<{ ok: true; workflow: WorkflowMeta }> {
  return api(`/workflows/${encodeURIComponent(wid)}`, { method: "PATCH", csrf, body });
}

export async function deleteWorkflow(
  wid: string,
  csrf: string,
): Promise<{ ok: true; id: string }> {
  return api(`/workflows/${encodeURIComponent(wid)}`, { method: "DELETE", csrf });
}

export async function putWorkflowYaml(
  wid: string,
  yaml: string,
  csrf: string,
): Promise<{ ok: true; id: string; graph: GraphNode[] }> {
  return api(`/workflows/${encodeURIComponent(wid)}/yaml`, { method: "PUT", csrf, body: { yaml } });
}

export async function addWorkflowNode(
  wid: string,
  node: Record<string, unknown>,
  csrf: string,
): Promise<WorkflowNodeResponse> {
  return api(`/workflows/${encodeURIComponent(wid)}/nodes`, { method: "POST", csrf, body: { node } });
}

export async function putWorkflowNode(
  wid: string,
  nodeId: string,
  node: Record<string, unknown>,
  csrf: string,
): Promise<WorkflowNodeResponse> {
  return api(`/workflows/${encodeURIComponent(wid)}/nodes/${encodeURIComponent(nodeId)}`, {
    method: "PUT",
    csrf,
    body: { node },
  });
}

export async function deleteWorkflowNode(
  wid: string,
  nodeId: string,
  csrf: string,
): Promise<WorkflowNodeResponse> {
  return api(`/workflows/${encodeURIComponent(wid)}/nodes/${encodeURIComponent(nodeId)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function listRuns(
  wid: string,
  signal?: AbortSignal,
): Promise<{ wid: string; count: number; runs: RunMeta[] }> {
  return api(`/workflows/${encodeURIComponent(wid)}/runs`, { signal });
}

export async function getRun(
  wid: string,
  rid: string,
  signal?: AbortSignal,
): Promise<RunDetailResponse> {
  return api(`/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}`, { signal });
}

export async function deleteRun(
  wid: string,
  rid: string,
  csrf: string,
): Promise<{ ok: true; rid: string }> {
  return api(`/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function putWorkflowSchedule(
  wid: string,
  schedule: { cron: string; timezone?: string; overrun?: string },
  csrf: string,
): Promise<{ ok: true; schedule: { cron: string; timezone: string; overrun: string } }> {
  return api(`/workflows/${encodeURIComponent(wid)}/schedule`, {
    method: "PUT",
    csrf,
    body: { cron: schedule.cron, timezone: schedule.timezone ?? "UTC", overrun: schedule.overrun ?? "skip" },
  });
}

export async function deleteWorkflowSchedule(
  wid: string,
  csrf: string,
): Promise<{ ok: true }> {
  return api(`/workflows/${encodeURIComponent(wid)}/schedule`, { method: "DELETE", csrf });
}

// ── Design chat composer (CONCEPT-0115) ─────────────────────────────

/** One slash command of the workflow design chat (server table, never a client constant). */
export interface WorkflowDesignCommand {
  cmd: string;
  args: string;
  desc: string;
}

export async function getWorkflowDesignCommands(
  signal?: AbortSignal,
): Promise<{ commands: WorkflowDesignCommand[] }> {
  return api(`/workflow-design/commands`, { signal });
}

/** A stored workflow attachment as the server describes it: an id, never a path. */
export interface WorkflowAttachment {
  id: string;
  name: string;
  size: number;
  mime: string;
}

// Mirrors attachments_common.ATTACH_MAX_FILES: one request carries at most this many files.
const WORKFLOW_UPLOAD_BATCH = 10;

export async function uploadWorkflowAttachments(
  wid: string,
  files: File[],
  csrf: string,
): Promise<WorkflowAttachment[]> {
  const out: WorkflowAttachment[] = [];
  for (let i = 0; i < files.length; i += WORKFLOW_UPLOAD_BATCH) {
    const form = new FormData();
    for (const f of files.slice(i, i + WORKFLOW_UPLOAD_BATCH)) form.append("files", f, f.name);
    const res = await fetch(`${BASE}/workflows/${encodeURIComponent(wid)}/attachments`, {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
      credentials: "include",
      body: form,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => `HTTP ${res.status}`);
      let detail = text;
      try {
        const json = JSON.parse(text);
        if (json?.detail) detail = typeof json.detail === "string" ? json.detail : JSON.stringify(json.detail);
      } catch { /* keep the raw text */ }
      // A later batch failed: the earlier ones are stored but would never be shown, so they
      // would silently count toward the per-workflow cap. Drop them (best effort) before failing.
      await Promise.allSettled(out.map((a) => deleteWorkflowAttachment(wid, a.id, csrf)));
      throw new ApiError(res.status, detail);
    }
    out.push(...((await res.json()) as { attachments: WorkflowAttachment[] }).attachments);
  }
  return out;
}

export async function deleteWorkflowAttachment(wid: string, id: string, csrf: string): Promise<{ ok: true }> {
  return api(`/workflows/${encodeURIComponent(wid)}/attachments/${encodeURIComponent(id)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function cancelWorkflowRun(
  wid: string,
  rid: string,
  csrf: string,
): Promise<{ ok: true; status: "stopping" | "cancelled" }> {
  return api(`/workflows/${encodeURIComponent(wid)}/runs/${encodeURIComponent(rid)}/cancel`, {
    method: "POST",
    csrf,
  });
}

// ── Readiness (synthetic check, acceptance case, ready override) ────────────

export type ReadinessStatus = "pass" | "fail" | "unmeasured" | "info";

export interface ReadinessSignal {
  name: string;
  status: ReadinessStatus;
  detail: string;
  value: number | null;
}

export interface AcceptanceCase {
  inputs?: Record<string, unknown>;
  stub_responses?: Record<string, Record<string, unknown>>;
  expect: {
    state?: "complete" | "failed";
    nodes_reached?: string[];
    final_contains?: Record<string, string>;
  };
}

export interface ReadinessReport {
  ready: boolean;
  /** Always true: the check runs against a deterministic stub, never a real engine. */
  synthetic: boolean;
  run_state: string | null;
  elapsed_ms: number;
  signals: ReadinessSignal[];
  nodes: { node_id: string; node_type: string; status: "success" | "failed" | "skipped"; error: string | null }[];
  phase: string;
  acceptance: AcceptanceCase | null;
  notice: string;
}

export async function getReadiness(wid: string): Promise<ReadinessReport> {
  return api(`/workflows/${encodeURIComponent(wid)}/readiness`);
}

export async function putAcceptance(wid: string, acceptance: AcceptanceCase, csrf: string): Promise<{ ok: true }> {
  return api(`/workflows/${encodeURIComponent(wid)}/acceptance`, { method: "PUT", csrf, body: acceptance });
}

export async function postReadyOverride(
  wid: string,
  reason: string,
  csrf: string,
): Promise<{ ok: true; phase: "ready" }> {
  return api(`/workflows/${encodeURIComponent(wid)}/ready-override`, { method: "POST", csrf, body: { reason } });
}
