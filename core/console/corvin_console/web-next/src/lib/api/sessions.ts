/**
 * api/sessions — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── Web chat sessions (Iter 3a) ────────────────────────────────────

export interface ChatSessionSummary {
  sid: string;
  chat_key: string;
  title: string;
  created_at: number;
  last_active_at: number;
  turn_count: number;
  workdir: string;
}

export interface ChatSessionListResponse {
  tenant_id: string;
  count: number;
  sessions: ChatSessionSummary[];
}

export async function listChatSessions(signal?: AbortSignal): Promise<ChatSessionListResponse> {
  return api<ChatSessionListResponse>("/chat/sessions", { signal });
}

// ── WDAT Audit Trail (ADR-0109) ────────────────────────────────────────

export interface WdatRunSummary {
  run_id: string;
  workflow_id: string;
  status: string;
  is_active: boolean;
  started_at: number;
  total_workers: number;
  iterations: number;
  duration_s: number;
}

export interface WdatRunListResponse {
  sid: string;
  count: number;
  runs: WdatRunSummary[];
}

export interface WdatNodeData {
  label: string;
  // manager fields
  iteration?: number;
  decision_type?: string;
  decision_hash?: string;
  n_subtasks?: number;
  spawn_nonce?: string;
  model_id?: string;
  // worker fields
  worker_id?: string;
  depth?: number;
  parent_worker_id?: string | null;
  status?: string | null;
  confidence?: number | null;
  color?: string;
  instruction_hash?: string;
  output_hash?: string;
  duration_ms?: number | null;
  tokens_used?: number | null;
  tool_count?: number;
  engine_attestation?: {
    engine_id?: string;
    model_id?: string;
    locality?: string;
  };
  // wdat_engine node fields (engine_id + locality shared with engine_attestation but at top level)
  engine_id?: string;
  locality?: string;
  exit_code?: number | null;
  // tool-call node fields (wdat_tool, ADR-0109 M6)
  decision?: "allow" | "deny";
  seq?: number;
  // client-only: flashed after live merge, cleared after 1.5 s
  _isNew?: boolean;
}

export interface WdatGraphNode {
  id: string;
  type: "wdat_manager" | "wdat_worker" | "wdat_engine" | "wdat_tool";
  position: { x: number; y: number };
  data: WdatNodeData;
}

export interface WdatGraphEdge {
  id: string;
  source: string;
  target: string;
  animated?: boolean;
  style?: Record<string, unknown>;
  markerEnd?: Record<string, unknown>;
}

export interface WdatGraphMeta {
  run_id: string;
  // Real hash-chain verification (security_events.verify_chain) — "broken"
  // when the walk finds tampered/broken-link entries, "unavailable" when
  // the verifier itself could not be reached/run.
  chain_integrity: "verified" | "empty" | "broken" | "unavailable";
  total_workers: number;
  total_manager_decisions: number;
  eu_ai_act: {
    art_9_risk_management?: string;
    art_13_transparency?: string;
    art_14_human_oversight?: string;
  };
}

export interface WdatGraphPayload {
  mode: "wdat";
  nodes: WdatGraphNode[];
  edges: WdatGraphEdge[];
  meta: WdatGraphMeta;
}

export async function listSessionWdatRuns(
  sid: string,
  signal?: AbortSignal,
): Promise<WdatRunListResponse> {
  return api<WdatRunListResponse>(`/chat/sessions/${encodeURIComponent(sid)}/wdat`, { signal });
}

export async function getSessionWdatGraph(
  sid: string,
  runId: string,
  signal?: AbortSignal,
): Promise<WdatGraphPayload> {
  return api<WdatGraphPayload>(
    `/chat/sessions/${encodeURIComponent(sid)}/wdat/${encodeURIComponent(runId)}/graph`,
    { signal },
  );
}

// ── WDAT M6 — worker engine trace ────────────────────────────────────

export interface WorkerToolCall {
  seq:      number;
  ts:       number;
  tool:     string;
  decision: "allow" | "deny";
}

export interface WorkerTraceResponse {
  worker_id:  string;
  run_id:     string;
  tool_calls: WorkerToolCall[];
  summary: {
    total_calls:  number;
    denied_calls: number;
    error_calls:  number;
  };
}

// ── ADR-0118 — Chain Dual-Track View ─────────────────────────────────────────

export interface ChainAuditEvent {
  hash_prefix: string;
  event_type:  string;
  severity:    "INFO" | "WARNING" | "CRITICAL";
  ts:          number | null;
  details:     Record<string, unknown>;
}

export interface ChainDelegationGroup {
  delegation_id: string;
  engine:        string;
  genesis_match: boolean | null;
  os_events:     ChainAuditEvent[];
  worker_events: ChainAuditEvent[];
}

export interface ChainDualTrackPayload {
  session_id:    string;
  genesis:       { hash_prefix: string; network_id: string; instance_id: string; network_pubkey_fp: string } | null;
  delegations:   ChainDelegationGroup[];
  os_only_events: ChainAuditEvent[];
  // Real hash-chain verification (security_events.verify_chain) across the
  // underlying audit.jsonl file(s) — qualifies every delegation's
  // genesis_match reading: a genesis_match pulled from a broken/tampered
  // chain is not trustworthy regardless of which event-type string is in it.
  chain_verified: boolean;
  ts:            number;
}

export async function getChainDualTrack(
  sid: string,
  signal?: AbortSignal,
): Promise<ChainDualTrackPayload> {
  return api<ChainDualTrackPayload>(
    `/chat/sessions/${encodeURIComponent(sid)}/chain-dual-track`,
    { signal },
  );
}

export async function fetchWorkerTrace(
  sid: string,
  runId: string,
  workerId: string,
  signal?: AbortSignal,
): Promise<WorkerTraceResponse> {
  return api<WorkerTraceResponse>(
    `/chat/sessions/${encodeURIComponent(sid)}/wdat/${encodeURIComponent(runId)}/workers/${encodeURIComponent(workerId)}/trace`,
    { signal },
  );
}

// ── OS-Turn Audit (EU AI Act Art. 12/13) ─────────────────────────────

export interface OsToolEntry {
  name: string;
  seq:  number;
}

export interface OsTurn {
  turn_id:      string;
  persona:      string;
  started_at:   string;
  tools:        OsToolEntry[];   // tool name + seq, no inputs/outputs (GDPR Art. 5)
  completed:    boolean;
  duration_ms:  number;
  tools_called: number;
  exit_code:    number;
  timed_out:    boolean;
  model:        string;          // OS-engine model id (empty while running)
}

export interface OsTurnsResponse {
  sid:      string;
  chat_key: string;
  count:    number;
  turns:    OsTurn[];
}

export async function listSessionOsTurns(
  sid: string,
  signal?: AbortSignal,
): Promise<OsTurnsResponse> {
  return api<OsTurnsResponse>(`/chat/sessions/${encodeURIComponent(sid)}/os-turns`, { signal });
}

// ── ADR-0171 — Universal engine spans (engine-agnostic; OS + worker) ───

export interface EngineSpan {
  span_id:         string;
  parent_span_id?: string;
  role?:           "os" | "manager" | "worker";
  engine_id?:      string;
  model_id?:       string;
  run_id?:         string;
  turn_id?:        string;
  status?:         string;        // ok | error | "" while running
  duration_ms?:    number;
  tokens_used?:    number;
  tool_call_count?: number;
  completed:       boolean;
}

export interface EngineSpansResponse {
  sid:      string;
  chat_key: string;
  count:    number;
  engines:  string[];   // distinct engine_ids seen — "every engine audited"
  roles:    string[];
  spans:    EngineSpan[];
}

export async function listSessionEngineSpans(
  sid: string,
  role?: "os" | "manager" | "worker",
  signal?: AbortSignal,
): Promise<EngineSpansResponse> {
  const q = role ? `?role=${encodeURIComponent(role)}` : "";
  return api<EngineSpansResponse>(
    `/chat/sessions/${encodeURIComponent(sid)}/engine-spans${q}`, { signal });
}

// ── Execution Log — flat chronological OS + ACS event stream ──────────

export interface ExecLogEntry {
  ts:         number;
  ts_iso:     string;
  event_type: string;
  role:       "os" | "acs";
  details: {
    model?:           string;
    model_id?:        string;
    engine_id?:       string;
    duration_ms?:     number;
    tokens_used?:     number;
    tool_name?:       string;
    seq?:             number;
    tools_called?:    number;
    exit_code?:       number;
    timed_out?:       boolean;
    worker_id?:       string;
    run_id?:          string;
    turn_id?:         string;
    iteration?:       number;
    decision_type?:   string;
    status?:          string;
    workers_spawned?: number;
    passed?:          boolean;
    aggregate_score?: number;
    gate_count?:      number;
    confidence?:      number;
    loss_total?:      number;
    artifact_count?:  number;
    n_subtasks?:      number;
  };
}

export interface ExecLogResponse {
  sid:     string;
  chat_key: string;
  count:   number;
  entries: ExecLogEntry[];
}

export async function fetchExecutionLog(
  sid: string,
  signal?: AbortSignal,
): Promise<ExecLogResponse> {
  return api<ExecLogResponse>(
    `/chat/sessions/${encodeURIComponent(sid)}/execution-log`,
    { signal },
  );
}


// ── Chat Debug Log ─────────────────────────────────────────────────────────────
export interface DebugLogResponse {
  ok: boolean;
  sid: string;
  total_events: number;
  returned: number;
  events: object[];
}

export async function getSessionDebugLog(
  sid: string,
  signal?: AbortSignal,
  n = 500,
): Promise<DebugLogResponse> {
  return api<DebugLogResponse>(
    `/chat/sessions/${encodeURIComponent(sid)}/debug?n=${n}`,
    { signal },
  );
}


// ── ACO API (ADR-0174) ────────────────────────────────────────────────────────

export interface AnomalyItem {
  anomaly_class: string;
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  message: string;
  evidence_count: number;
  evidence: object[];
  suggestion: string;
}

export interface AnomalyScanResponse {
  ok: boolean;
  sid: string;
  total: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  anomalies: AnomalyItem[];
}

export async function getSessionAnomalies(
  sid: string,
  signal?: AbortSignal,
): Promise<AnomalyScanResponse> {
  return api<AnomalyScanResponse>(
    `/chat/sessions/${encodeURIComponent(sid)}/aco/anomalies`,
    { signal },
  );
}

export interface DiagnosisReport {
  anomaly_class: string;
  severity: string;
  layers: string[];
  hypothesis: string;
  repro_steps: string[];
  adr_refs: string[];
  evidence_count: number;
}

export interface DiagnosisResponse {
  ok: boolean;
  sid: string;
  anomaly_count: number;
  diagnosed_count: number;
  reports: DiagnosisReport[];
}

export async function getSessionDiagnosis(
  sid: string,
  signal?: AbortSignal,
): Promise<DiagnosisResponse> {
  return api<DiagnosisResponse>(
    `/chat/sessions/${encodeURIComponent(sid)}/aco/diagnosis`,
    { signal },
  );
}

export interface ReplayTurnResult {
  turn_index: number;
  input_preview: string;
  passed: boolean;
  error: string;
  missing_events: string[];
  missing_fields: string[];
  elapsed_ms: number | null;
}

export interface ReplayResponse {
  ok: boolean;
  sid: string;
  scenario: string;
  passed: boolean;
  summary: string;
  turns_in_log: number;
  turns: ReplayTurnResult[];
}

export async function validateReplayManifest(
  sid: string,
  manifest: object,
  signal?: AbortSignal,
): Promise<ReplayResponse> {
  return api<ReplayResponse>(
    `/chat/sessions/${encodeURIComponent(sid)}/aco/replay`,
    { method: "POST", body: manifest, signal },
  );
}

export interface RepairAction {
  action_id: string;
  anomaly_class: string;
  status: "applied" | "skipped" | "dry_run" | "error";
  detail: string;
  events_written?: number;
}

export interface RepairResponse {
  ok: boolean;
  sid: string;
  dry_run: boolean;
  before: { critical: number; high: number };
  after: { critical: number; high: number };
  delta_loss: number;
  convergence_reached: boolean;
  actions_applied: RepairAction[];
  actions_skipped: RepairAction[];
  total_events_written: number;
}

export async function repairSession(
  sid: string,
  dryRun = false,
  signal?: AbortSignal,
): Promise<RepairResponse> {
  return api<RepairResponse>(
    `/chat/sessions/${encodeURIComponent(sid)}/aco/repair`,
    { method: "POST", body: { dry_run: dryRun }, signal },
  );
}

export interface InstanceStatsResponse {
  active_7d: number;
  active_30d: number;
  updated_at: string;
}

export async function getInstanceStats(signal?: AbortSignal): Promise<InstanceStatsResponse> {
  // Proxy through the local console backend, which resolves the features-server
  // URL via the single Python resolver (instance_identity._features_server).
  // The old direct fetch to api.corvin-labs.com is dead (NXDOMAIN) — the backend
  // moved to the Railway deployment.
  const res = await fetch("/v1/console/instance-stats", { signal });
  if (!res.ok) throw new Error(`stats fetch failed: ${res.status}`);
  return res.json();
}
