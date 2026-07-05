/**
 * Thin fetch wrapper for the console REST API.
 *
 * Contract from ADR-0015 / ADR-0037:
 *   • Cookie `corvin_console_sid` is set by the backend on /auth/login
 *     and carried automatically with `credentials: "include"`.
 *   • Mutating requests carry `X-CSRF-Token` (value returned by
 *     /auth/login + /auth/whoami).
 */

const BASE = "/v1/console";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, detail: unknown) {
    const detailStr =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "detail" in detail
          ? formatDetailMessage((detail as { detail: unknown }).detail)
          : `HTTP ${status}`;
    super(detailStr);
    this.status = status;
    this.detail = detail;
  }
}

// Helper to format detail messages without stringifying object arrays
function formatDetailMessage(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((e: unknown) => {
        if (typeof e === "string") return e;
        if (e && typeof e === "object") {
          const obj = e as { msg?: unknown; message?: unknown };
          return String(obj.msg || obj.message || String(e));
        }
        return String(e);
      })
      .filter((msg) => msg !== "[object Object]");
    return messages.length > 0
      ? `Validation error: ${messages.join(", ")}`
      : String(detail);
  }
  return String(detail);
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  body?: unknown;
  csrf?: string;
  signal?: AbortSignal;
  /** Override the default request timeout (ms). Pass 0 to disable. */
  timeoutMs?: number;
}

// Default wall-clock budget for a single console API call. Without this a
// hung backend leaves react-query queries pending forever, which the UI
// renders as a perpetual "Loading…" spinner. With it, a stalled request
// rejects and surfaces through the route error boundary instead.
const DEFAULT_TIMEOUT_MS = 30_000;

/**
 * Combine the caller's AbortSignal (react-query cancellation) with a
 * timeout, without relying on AbortSignal.any (not yet universal). Returns
 * the merged signal plus a cleanup() to clear the timer / listener.
 */
function withTimeout(
  signal: AbortSignal | undefined,
  timeoutMs: number,
): { signal: AbortSignal; cleanup: () => void } {
  if (!timeoutMs || timeoutMs <= 0) {
    return { signal: signal ?? new AbortController().signal, cleanup: () => {} };
  }
  const controller = new AbortController();
  const onAbort = () => controller.abort(signal?.reason);
  const timer = setTimeout(
    () => controller.abort(new DOMException("Request timed out", "TimeoutError")),
    timeoutMs,
  );
  if (signal) {
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });
  }
  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    },
  };
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (opts.csrf) {
    headers["X-CSRF-Token"] = opts.csrf;
  }

  const { signal, cleanup } = withTimeout(
    opts.signal,
    opts.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  );

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: opts.method ?? "GET",
      headers,
      credentials: "include",
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal,
    });
  } finally {
    cleanup();
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let payload: unknown = text;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      /* keep as text */
    }
  }

  if (!res.ok) {
    throw new ApiError(res.status, payload);
  }
  return payload as T;
}

// ── Typed endpoints ────────────────────────────────────────────────

export interface WhoamiResponse {
  tier: "owner";
  tenant_id: string;
  fingerprint: string;
  csrf_token: string;
  expires_at: number;
}

export async function whoami(signal?: AbortSignal): Promise<WhoamiResponse> {
  return api<WhoamiResponse>("/auth/whoami", { signal });
}

export async function logout(csrf: string): Promise<void> {
  await api<void>("/auth/logout", { method: "POST", csrf });
}

export interface PersonaSummary {
  name: string;
  source: "bundle" | "user";
  description: string;
  permission_mode: string | null;
  default_engine: string | null;
  engine?: string | null;
  os_model?: string | null;
  worker_model?: string | null;
  engine_lock?: boolean;
  model: string | null;
  tool_namespace: string | null;
  forge_enabled: boolean;
  skill_forge_enabled: boolean;
  inject_skills?: boolean;
  ldd_preset: string | null;
  mcp_count: number;
  tools_allowed: number;
  tools_disallowed: number;
  disabled?: boolean;
  path: string;
}

export interface PersonaListResponse {
  tenant_id: string;
  count: number;
  personas: PersonaSummary[];
}

export async function listPersonas(signal?: AbortSignal): Promise<PersonaListResponse> {
  return api<PersonaListResponse>("/personas", { signal });
}

export interface DashboardBridgeStatus {
  channel: string;
  configured: boolean;
  has_token: boolean;
  source: "canonical" | "legacy" | null;
}

export interface DashboardEngineStatus {
  installed: boolean;
  has_credential: boolean;
}

export interface DashboardResponse {
  tenant_id: string;
  ts: number;
  engine_default: string;
  engine_status: Record<string, DashboardEngineStatus>;
  stt: { mode: "pinned" | "chain"; providers: string[] };
  bridges: DashboardBridgeStatus[];
  audit_chain: {
    present: boolean;
    size_bytes?: number;
    last_event_type?: string | null;
    last_event_ts?: number | null;
  };
  today_counts: Record<string, number>;
  fingerprint: string;
  expires_at: number;
}

export async function dashboard(signal?: AbortSignal): Promise<DashboardResponse> {
  return api<DashboardResponse>("/dashboard", { signal });
}

// Landing-personas is a NEW endpoint (Iteration 1, task 5) — exposed
// unauthenticated so the public hero can render the gallery without a
// login. Backend returns only the curated "publishable" projection
// (name + description + tool_namespace + ldd_preset + forge_enabled).
export interface LandingPersona {
  name: string;
  description: string;
  tool_namespace: string | null;
  forge_enabled: boolean;
  skill_forge_enabled: boolean;
  ldd_preset: string | null;
}

export interface LandingPersonasResponse {
  count: number;
  personas: LandingPersona[];
}

export async function landingPersonas(signal?: AbortSignal): Promise<LandingPersonasResponse> {
  return api<LandingPersonasResponse>("/landing/personas", { signal });
}

// ── Persona detail / mutations ─────────────────────────────────────

export interface PersonaDetailResponse {
  name: string;
  source: "bundle" | "user";
  path: string;
  body: Record<string, unknown>;
  editable: boolean;
  disabled?: boolean;
}

export async function getPersona(
  name: string,
  signal?: AbortSignal,
): Promise<PersonaDetailResponse> {
  return api<PersonaDetailResponse>(`/personas/${encodeURIComponent(name)}`, { signal });
}

export async function updatePersona(
  name: string,
  body: Record<string, unknown>,
  csrf: string,
): Promise<{ name: string; source: "user"; path: string; ok: true }> {
  return api(`/personas/${encodeURIComponent(name)}`, {
    method: "PUT",
    csrf,
    body: { body },
  });
}

export async function copyPersonaFromBundle(
  name: string,
  csrf: string,
): Promise<{ name: string; copied: boolean; path: string; ok: true }> {
  return api(`/personas/${encodeURIComponent(name)}/copy-from-bundle`, {
    method: "POST",
    csrf,
  });
}

// Create a brand-new user-scope persona. The backend PUT is create-or-replace,
// so creation reuses it — the body just carries a fresh name not present in the
// bundle or user dir.
export async function createPersona(
  name: string,
  body: Record<string, unknown>,
  csrf: string,
): Promise<{ name: string; source: "user"; ok: true }> {
  return api(`/personas/${encodeURIComponent(name)}`, {
    method: "PUT",
    csrf,
    body: { body },
  });
}

export async function deletePersona(
  name: string,
  csrf: string,
): Promise<{ name: string; deleted: boolean; reverted_to_bundle: boolean; ok: true }> {
  return api(`/personas/${encodeURIComponent(name)}`, {
    method: "DELETE",
    csrf,
  });
}

// Deactivate / reactivate a persona — name-level per-tenant registry, so it
// works for bundle personas too (no file copy). A disabled persona is dropped
// from runtime auto-routing and shown "off" in the console.
export async function setPersonaDisabled(
  name: string,
  disabled: boolean,
  csrf: string,
): Promise<{ name: string; disabled: boolean; ok: true }> {
  const action = disabled ? "disable" : "enable";
  return api(`/personas/${encodeURIComponent(name)}/${action}`, {
    method: "POST",
    csrf,
  });
}

// ── Bridges ────────────────────────────────────────────────────────

export interface BridgeListItem {
  channel: string;
  configured: boolean;
  enabled: boolean;
  path: string;
  size_bytes: number;
}

export interface BridgeListResponse {
  count: number;
  bridges: BridgeListItem[];
}

export async function listBridges(signal?: AbortSignal): Promise<BridgeListResponse> {
  return api<BridgeListResponse>("/bridges", { signal });
}

export interface BridgeSettingsResponse {
  channel: string;
  path: string;
  exists: boolean;
  settings: Record<string, unknown>;
}

export async function getBridgeSettings(
  channel: string,
  signal?: AbortSignal,
): Promise<BridgeSettingsResponse> {
  return api<BridgeSettingsResponse>(
    `/bridges/${encodeURIComponent(channel)}/settings`,
    { signal },
  );
}

export async function putBridgeSettings(
  channel: string,
  settings: Record<string, unknown>,
  csrf: string,
  reAuthToken?: string,
): Promise<{ channel: string; path: string; ok: true }> {
  return api(`/bridges/${encodeURIComponent(channel)}/settings`, {
    method: "PUT",
    csrf,
    body: { settings, re_auth_token: reAuthToken || null },
  });
}

export interface BridgeEnabledResponse {
  channel: string;
  enabled: boolean;
  restart_needed: boolean;
  supervisor: { applied: boolean; via?: string; reason?: string; output?: string };
  ok: true;
}

export async function setBridgeEnabled(
  channel: string,
  enabled: boolean,
  csrf: string,
  reAuthToken?: string,
): Promise<BridgeEnabledResponse> {
  return api<BridgeEnabledResponse>(
    `/bridges/${encodeURIComponent(channel)}/enabled`,
    {
      method: "PUT",
      csrf,
      body: { enabled, re_auth_token: reAuthToken || null },
    },
  );
}

// ── Profile (Identity + Voice Audience) ────────────────────────────

export type AudienceLevel = "novice" | "intermediate" | "expert";
export type AudienceStyle = "concise" | "verbose" | "example-driven";
export type AudienceToggle = "on" | "off";

export interface IdentityFields {
  name?: string | null;
  display_language?: string | null;
  tone?: string | null;
  timezone?: string | null;
  default_persona?: string | null;
  voice_note_max_sentences?: number | null;
  custom_instructions?: string | null;
}

export interface AudienceFields {
  voice_audience_level?: AudienceLevel | null;
  voice_audience_jargon?: number | null;
  voice_audience_style?: AudienceStyle | null;
  voice_audience_background?: string | null;
  voice_audience_metaphors?: AudienceToggle | null;
  voice_audience_domains?: string[] | null;
  voice_audience_learning?: number | null;
  voice_audience_chat_render?: AudienceToggle | null;
  tts_voice?: string | null;
  tts_voice_de?: string | null;
  tts_voice_en?: string | null;
  /** TTS provider selection: "auto" | "openai" | "edge" | "piper" */
  tts_provider?: string | null;
}

export interface ProfileSnapshot {
  identity: IdentityFields;
  audience: AudienceFields;
  extra: Record<string, unknown>;
}

export interface ProfileResponse {
  tenant_id: string;
  profile: ProfileSnapshot;
  preview_de: string;
  preview_en: string;
  system_block: string;
  schema: Record<string, unknown>;
}

export async function getProfile(signal?: AbortSignal): Promise<ProfileResponse> {
  return api<ProfileResponse>("/profile", { signal });
}

export async function putProfile(
  body: {
    identity?: IdentityFields | null;
    audience?: AudienceFields | null;
  },
  csrf: string,
): Promise<ProfileResponse> {
  return api<ProfileResponse>("/profile", {
    method: "PUT",
    csrf,
    body: { ...body },
  });
}

export async function previewProfile(
  audience: AudienceFields,
  lang: string,
  csrf: string,
): Promise<{ ok: true; lang: string; block: string; empty: boolean }> {
  return api("/profile/preview", {
    method: "POST",
    csrf,
    body: { audience, lang },
  });
}

export async function resetProfile(
  csrf: string,
): Promise<{ ok: true; profile: ProfileSnapshot }> {
  return api("/profile/reset", {
    method: "POST",
    csrf,
    body: {},
  });
}

export async function testVoice(
  voice: string,
  lang: string,
  csrf: string,
): Promise<{ ok: true; voice: string; lang: string; audio_base64: string; mime_type: string }> {
  return api("/voice-test", {
    method: "POST",
    csrf,
    body: { voice, lang },
  });
}

// ── Forge tools + SkillForge skills ────────────────────────────────

export type PromoteTarget = "session" | "project" | "user";

export interface ForgeToolSummary {
  name: string;
  description: string;
  scope: string;
  scope_source: string;
  runtime: string;
  promoted: boolean;
  call_count: number;
  created_at: number | null;
  sha256: string;
  param_count: number;
  param_names: string[];
  required: string[];
  impl_path: string | null;
}

export interface ForgeToolListResponse {
  tenant_id: string;
  ts: number;
  count: number;
  tools: ForgeToolSummary[];
}

export async function listForgeTools(signal?: AbortSignal): Promise<ForgeToolListResponse> {
  return api<ForgeToolListResponse>("/tools", { signal });
}

export interface ForgeToolDetailResponse {
  name: string;
  scope_source: string;
  registry_path: string | null;
  entry: Record<string, unknown>;
  impl_preview: string | null;
}

export async function getForgeTool(
  name: string,
  signal?: AbortSignal,
): Promise<ForgeToolDetailResponse> {
  return api<ForgeToolDetailResponse>(`/tools/${encodeURIComponent(name)}`, { signal });
}

export async function promoteForgeTool(
  name: string,
  to: PromoteTarget,
  csrf: string,
  force = false,
): Promise<{ name: string; to: PromoteTarget; ok: true; promoted: true }> {
  return api(`/tools/${encodeURIComponent(name)}/promote`, {
    method: "POST",
    csrf,
    body: { to, force },
  });
}

export interface SkillSummary {
  name: string;
  scope: string;
  scope_source: string;
  type: string;
  description: string;
  created_at: number | null;
  grade_count: number;
  mean_score: number | null;
  sha256: string;
  skill_dir: string;
}

export interface SkillListResponse {
  tenant_id: string;
  ts: number;
  count: number;
  skills: SkillSummary[];
}

export async function listSkills(signal?: AbortSignal): Promise<SkillListResponse> {
  return api<SkillListResponse>("/skills", { signal });
}

export interface SkillDetailResponse {
  name: string;
  scope_source: string;
  skill_dir: string;
  meta: Record<string, unknown>;
  body_preview: string | null;
}

export async function getSkill(
  name: string,
  signal?: AbortSignal,
): Promise<SkillDetailResponse> {
  return api<SkillDetailResponse>(`/skills/${encodeURIComponent(name)}`, { signal });
}

export async function promoteSkill(
  name: string,
  to: PromoteTarget,
  csrf: string,
  force = false,
): Promise<{ name: string; to: PromoteTarget; ok: true; promoted: true }> {
  return api(`/skills/${encodeURIComponent(name)}/promote`, {
    method: "POST",
    csrf,
    body: { to, force },
  });
}

// ── LDD layer toggles ──────────────────────────────────────────────

export interface LddLayer {
  id: string;
  label: string;
  configured: boolean;
  effective: boolean;
  depends_on: string | null;
}

export interface LddSnapshot {
  layers: LddLayer[];
  master_enabled: boolean;
  presets: string[];
  depends_on: Record<string, string>;
  /** True when the LDD_AUTO_OPTIN=1 env var is active on the server.
   *  In this mode the env var overrides file-based toggles; writes via PUT
   *  take effect on disk but the effective state remains forced-on. */
  auto_optin_active?: boolean;
}

export async function getLdd(signal?: AbortSignal): Promise<LddSnapshot> {
  return api<LddSnapshot>("/ldd", { signal });
}

export async function setLddMaster(
  enabled: boolean,
  csrf: string,
): Promise<LddSnapshot> {
  return api<LddSnapshot>("/ldd/master", {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}

export async function setLddLayer(
  layer: string,
  enabled: boolean,
  csrf: string,
): Promise<LddSnapshot> {
  return api<LddSnapshot>(`/ldd/layers/${encodeURIComponent(layer)}`, {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}

export async function applyLddPreset(
  name: string,
  csrf: string,
): Promise<LddSnapshot> {
  return api<LddSnapshot>(`/ldd/presets/${encodeURIComponent(name)}`, {
    method: "POST",
    csrf,
    body: {},
  });
}

// ── Quality Layers (ADR Gate, etc.) ────────────────────────────────

export interface QualityLayer {
  id: string;
  name: string;
  configured: boolean;
  category: "quality" | "ldd";
}

export interface QualityLayersSnapshot {
  globally_enabled: boolean;
  layers: QualityLayer[];
}

export async function getQualityLayers(signal?: AbortSignal): Promise<QualityLayersSnapshot> {
  return api<QualityLayersSnapshot>("/quality-layers", { signal });
}

export async function setQualityLayerMaster(
  enabled: boolean,
  csrf: string,
): Promise<QualityLayersSnapshot> {
  return api<QualityLayersSnapshot>("/quality-layers/master", {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}

export async function setQualityLayer(
  layer: string,
  enabled: boolean,
  csrf: string,
): Promise<QualityLayersSnapshot> {
  return api<QualityLayersSnapshot>(`/quality-layers/layers/${encodeURIComponent(layer)}`, {
    method: "PUT",
    csrf,
    body: { enabled },
  });
}

// ── Audit tail + members (for Compliance page) ────────────────────

export interface AuditEvent {
  ts: number | null;
  event_type: string;
  severity: string;
  hash_prefix: string | null;
  run_id: string | null;
  tool: string | null;
  details: Record<string, unknown>;
}

export interface AuditTailResponse {
  tenant_id: string;
  ts: number;
  count: number;
  chain_size_b?: number;
  events: AuditEvent[];
}

export async function auditTail(
  params: { limit?: number; severity?: string; eventPrefix?: string } = {},
  signal?: AbortSignal,
): Promise<AuditTailResponse> {
  const q = new URLSearchParams();
  if (params.limit) q.set("limit", String(params.limit));
  if (params.severity) q.set("severity", params.severity);
  if (params.eventPrefix) q.set("event_prefix", params.eventPrefix);
  const qs = q.toString();
  return api<AuditTailResponse>(`/audit/tail${qs ? `?${qs}` : ""}`, { signal });
}

/**
 * One row per chat. Mirrors the actual `/v1/console/members` route in
 * `routes/members.py::members_list` — NOT a per-uid grant list (that
 * lives behind `/members/{chat_key}` and is not surfaced yet).
 */
export interface MembersChatSummary {
  chat_key: string;
  channel: string;
  chat: string;
  members: number;
  bundles: Record<string, number>;
  quota_entries: number;
  consent_entries: number;
  disclosure_entries: number;
}

export interface MembersListResponse {
  tenant_id: string;
  ts: number;
  count: number;
  chats: MembersChatSummary[];
}

export async function listMembers(signal?: AbortSignal): Promise<MembersListResponse> {
  return api<MembersListResponse>("/members", { signal });
}

export interface MemberUidRecord {
  uid: string;
  role: Record<string, unknown> | null;
  quota: Record<string, unknown> | null;
  consent: Record<string, unknown> | null;
  disclosure: Record<string, unknown> | null;
}

export interface MembersDetailResponse {
  tenant_id: string;
  ts: number;
  chat_key: string;
  channel: string;
  chat: string;
  uid_count: number;
  uids: MemberUidRecord[];
}

export async function getMembersDetail(
  chatKey: string,
  signal?: AbortSignal,
): Promise<MembersDetailResponse> {
  return api<MembersDetailResponse>(`/members/${encodeURIComponent(chatKey)}`, { signal });
}

// ── ADR-0062 M7: Workflow explanation ────────────────────────────────────

export async function explainWorkflow(
  wid: string,
  csrf: string,
): Promise<{ ok: boolean; explanation: string; cached: boolean }> {
  return api(`/workflows/${encodeURIComponent(wid)}/explain`, { method: "POST", csrf });
}

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
  chain_integrity: "verified" | "empty";
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

// ── License management (ADR-0017 Phase IV) ────────────────────────────

export interface LicenseStatus {
  tier: string;
  mode: "free" | "active" | "grace" | "expired" | "invalid";
  expires_at: number | null;
  grace_ends_at: number | null;
  customer_fp: string | null;
  feature_flags: string[];
}

export async function getLicenseStatus(signal?: AbortSignal): Promise<LicenseStatus> {
  return api<LicenseStatus>("/license/status", { signal });
}

/** ADR-0092 full licence state — limits, features, custom per-customer config. */
export interface LicenseInfo {
  tier: string;
  loaded: boolean;
  issued_to: string | null;
  expires_at: number | null;
  subscription_active_until: number | null;
  jti_prefix: string | null;
  limits: Record<string, number | string[] | boolean | null>;
  features: Record<string, boolean>;
  custom: Record<string, unknown>;
  free_tier: Record<string, number | string[] | boolean | null>;
}

export async function getLicenseInfo(signal?: AbortSignal): Promise<LicenseInfo> {
  return api<LicenseInfo>("/license/info", { signal });
}

export interface LicenseUploadResponse {
  ok: boolean;
  tier: string;
  customer_fp: string;
  expires_at: number;
}

export async function uploadLicense(
  file: File,
  csrf: string,
): Promise<LicenseUploadResponse> {
  const form = new FormData();
  form.append("file", file);

  const headers: Record<string, string> = {
    "X-CSRF-Token": csrf,
  };

  const res = await fetch(`${BASE}/license/upload`, {
    method: "POST",
    headers,
    credentials: "include",
    body: form,
  });

  const text = await res.text();
  let payload: unknown = text;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      /* keep as text */
    }
  }

  if (!res.ok) {
    throw new ApiError(res.status, payload);
  }
  return payload as LicenseUploadResponse;
}

export async function revokeLicense(
  reason: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>("/license/revoke", {
    method: "POST",
    body: { reason },
    csrf,
  });
}

export interface LicenseKeyResponse {
  ok: boolean;
  tier: string;
  loaded: boolean;
  issued_to: string | null;
  expires_at: number | null;
}

export async function applyLicenseKey(
  key: string,
  csrf: string,
): Promise<LicenseKeyResponse> {
  return api<LicenseKeyResponse>("/license/key", {
    method: "POST",
    body: { key },
    csrf,
  });
}

export interface LicenseAuditEvent {
  timestamp: number;
  event_type: string;
  details: Record<string, unknown>;
}

export async function getLicenseAudit(
  limit?: number,
  signal?: AbortSignal,
): Promise<LicenseAuditEvent[]> {
  const query = limit ? `?limit=${limit}` : "";
  return api<LicenseAuditEvent[]>(`/license/audit-tail${query}`, { signal });
}

export async function createChatSession(
  csrf: string,
  title = "",
): Promise<{ ok: true; session: ChatSessionSummary }> {
  return api("/chat/sessions", {
    method: "POST",
    csrf,
    body: { title },
  });
}

export async function deleteChatSession(
  sid: string,
  csrf: string,
): Promise<{ ok: true; sid: string }> {
  return api(`/chat/sessions/${encodeURIComponent(sid)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function updateChatSessionTitle(
  sid: string,
  title: string,
  csrf: string,
): Promise<{ ok: true; session: ChatSessionSummary }> {
  return api(`/chat/sessions/${encodeURIComponent(sid)}`, {
    method: "PATCH",
    csrf,
    body: { title },
  });
}

export interface ChatTurnPart {
  kind: "text" | "tool" | "artifact";
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  path?: string;
  mime?: string;
  size?: number;
  label?: string;  // M5 (ADR-0170): provenance badge
}

export interface ChatTurn {
  role: "user" | "assistant" | "system";
  ts: number;
  parts: ChatTurnPart[];
}

export interface ChatTurnsResponse {
  sid: string;
  count: number;
  turns: ChatTurn[];
}

export async function getChatTurns(
  sid: string,
  limit = 200,
  signal?: AbortSignal,
): Promise<ChatTurnsResponse> {
  return api<ChatTurnsResponse>(
    `/chat/sessions/${encodeURIComponent(sid)}/turns?limit=${limit}`,
    { signal },
  );
}

// ── Voice (Iter 3b) ────────────────────────────────────────────────

export interface TranscribeResponse {
  ok: true;
  text: string;
  lang: string | null;
  provider: string;
  elapsed_ms: number;
  bytes: number;
}

export async function transcribeAudio(
  blob: Blob,
  csrf: string,
  lang?: string,
): Promise<TranscribeResponse> {
  const form = new FormData();
  form.append("audio", blob, "recording.webm");
  if (lang) form.append("lang", lang);
  const res = await fetch("/v1/console/voice/transcribe", {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrf },
    body: form,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text);
  }
  return res.json();
}

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
  status: "running" | "complete" | "failed";
  dry_run: boolean;
  started_at: number;
  finished_at: number | null;
  ok: boolean | null;
  error: string | null;
}

export interface RunDetailResponse {
  run: RunMeta;
  events: WorkflowRunEvent[];
}

export interface WorkflowRunEvent {
  type: "node_started" | "node_completed" | "node_failed" | "node_awaiting_approval" | "run_completed" | "error" | "media" | "table";
  ts: number;
  node_id?: string;
  tokens?: number;
  elapsed_s?: number;
  error?: string;
  message?: string;
  timeout_s?: number;
  ok?: boolean;
  dry_run?: boolean;
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
  body: { tool_name: string; strategy: string; budget: Record<string, unknown>; objective: string; params?: Record<string, unknown> },
  csrf: string,
): Promise<{ ok: true; run_id: string; manifest: Record<string, unknown> }> {
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

// ── Connectors (ADR-0039 Phase 8) ──────────────────────────────────

export interface ConnectorSummary {
  id: string;
  name: string;
  category: string;
  kind: "session_mcp" | "api_key_mcp";
  icon: string;
  description: string;
  capabilities: string[];
  example_instruction: string;
  enabled: boolean;
  status: "connected" | "disabled" | "needs_key";
  api_key_label: string | null;
  api_key_set: boolean;
  config_extra: Record<string, { label: string; default: string }>;
  extra_values: Record<string, string>;
}

export interface ConnectorListResponse {
  tenant_id: string;
  count: number;
  connectors: ConnectorSummary[];
  connected_ids: string[];
}

// ── Setup / Bridge guides / Engine keys ────────────────────────────

export interface EngineInfo {
  id: string;
  label: string;
  kind: "oauth" | "api_key" | "url";
  key: string | null;
  url: string;
  configured: boolean;
  value_masked: string | null;
}

export interface EnginesResponse {
  engines: EngineInfo[];
  env_path: string;
}

export async function listEngines(signal?: AbortSignal): Promise<EnginesResponse> {
  return api<EnginesResponse>("/setup/engines", { signal });
}

export async function updateEngineKey(
  engineId: string,
  value: string,
  csrf: string,
): Promise<{ ok: true; engine_id: string; key: string }> {
  return api(`/setup/engines/${encodeURIComponent(engineId)}`, {
    method: "PUT",
    csrf,
    body: { value },
  });
}

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

export interface BridgeSetupInfo {
  channel: string;
  configured: boolean;
  current_token_masked: string;
  qr_available: boolean;
  qr_url: string | null;
  guide: {
    display: string;
    steps: string[];
    field_label: string | null;
    field_placeholder: string | null;
    token_key: string | null;
    setup_url: string | null;
  };
}

export async function getBridgeSetup(channel: string, signal?: AbortSignal): Promise<BridgeSetupInfo> {
  return api<BridgeSetupInfo>(`/setup/bridge/${encodeURIComponent(channel)}`, { signal });
}

export interface WhatsappStartResult {
  ok: boolean;
  pid?: number;
  already_running?: boolean;
  node_missing?: boolean;
  error?: string;
  node_steps?: { platform: string; download_url: string; steps: string[] };
}

interface WhatsappStartStatus {
  state: "idle" | "running" | "done" | "error";
  phase?: string;
  result?: WhatsappStartResult;
}

/**
 * Start the WhatsApp bridge daemon from the UI (installs Node.js + deps on
 * demand). The daemon start + npm install can take a minute, so the server runs
 * it in a background thread; this starts it then polls the status to a terminal
 * state. `onPhase` receives live phase strings. Once the daemon is up, the QR
 * appears via the /setup/bridge/whatsapp poll (qr_available → <img>).
 */
export async function startWhatsappBridge(
  csrf: string,
  onPhase?: (phase: string) => void,
): Promise<WhatsappStartResult> {
  await api<WhatsappStartStatus>("/setup/whatsapp/start", {
    method: "POST",
    csrf,
    timeoutMs: 20_000,
  });
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 200; i++) {
    await sleep(2_000);
    let s: WhatsappStartStatus;
    try {
      s = await api<WhatsappStartStatus>("/setup/whatsapp/start/status", { timeoutMs: 15_000 });
    } catch {
      continue;
    }
    if (s.phase) onPhase?.(s.phase);
    if (s.state === "done" || s.state === "error") {
      return s.result ?? { ok: s.state === "done", error: s.state === "error" ? "Start failed" : undefined };
    }
  }
  return { ok: false, error: "Timed out starting the WhatsApp bridge — check that Node.js is installed." };
}

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
  telemetry_enabled: boolean;
  healing_enabled: boolean;
  risky_enabled: boolean;
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

// ── Chat settings (cowork per-chat persona pinning) ─────────────────

export interface ChatSettingsSummary {
  channel: string;
  chat_key: string;
  persona: string | null;
  ldd_enabled: boolean | null;
  dialectic_enabled: boolean | null;
}

export interface ChatSettingsListResponse {
  tenant_id: string;
  count: number;
  chats: ChatSettingsSummary[];
  known_channels: string[];
}

export async function listChatSettings(
  signal?: AbortSignal,
): Promise<ChatSettingsListResponse> {
  return api<ChatSettingsListResponse>("/chat-settings", { signal });
}

export async function patchChatSettings(
  channel: string,
  chatKey: string,
  patch: { persona?: string | null; ldd_enabled?: boolean; dialectic_enabled?: boolean },
  csrf: string,
): Promise<{ ok: true }> {
  return api(`/chat-settings/${encodeURIComponent(channel)}/${encodeURIComponent(chatKey)}`, {
    method: "PATCH",
    csrf,
    body: { ...patch },
  });
}

export async function listConnectors(signal?: AbortSignal): Promise<ConnectorListResponse> {
  return api<ConnectorListResponse>("/connectors", { signal });
}

export async function updateConnector(
  cid: string,
  body: { enabled: boolean; api_key?: string; extra?: Record<string, string> },
  csrf: string,
): Promise<{ ok: true; id: string; enabled: boolean }> {
  return api(`/connectors/${encodeURIComponent(cid)}`, { method: "PUT", csrf, body });
}

export interface MessengerChat {
  id: string;
  name: string;
  label: string;
  guild?: string;
  source: "api" | "inbox";
}

export async function listMessengerChats(
  messenger: string,
  signal?: AbortSignal,
): Promise<{ chats: MessengerChat[]; count: number; messenger: string }> {
  return api(`/connectors/${encodeURIComponent(messenger)}/chats`, { signal });
}

export async function importWorkflow(
  file: File,
  csrf: string,
): Promise<{ ok: true; id: string; workflow: WorkflowMeta; graph: GraphNode[] }> {
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await fetch(`${BASE}/workflows/import`, {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrf },
    body: form,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text);
  }
  return res.json();
}

// ── Agent Hub — A2A remote-trigger (Layer 38) ──────────────────────

export interface A2AOrigin {
  origin_id: string;
  enabled: boolean;
  spawn_worker: boolean;
  max_ttl_s: number | null;
  allowed_personas: string[];
  state?: "PENDING" | "ACTIVE";
  label?: string | null;
  _friendship?: boolean;
}

export interface A2AOriginsResponse {
  ts: number;
  count: number;
  origins: A2AOrigin[];
}

export async function getA2AOrigins(signal?: AbortSignal): Promise<A2AOriginsResponse> {
  return api<A2AOriginsResponse>("/remote-trigger/origins", { signal });
}

export interface A2AEndpoint {
  endpoint_id: string;
  url: string | null;
  instance_id_pin: string;
  enabled: boolean;
  default_ttl_s: number | null;
  state?: "PENDING" | "ACTIVE";
  label?: string | null;
  _friendship?: boolean;
}

export interface A2AEndpointsResponse {
  ts: number;
  count: number;
  endpoints: A2AEndpoint[];
}

export async function getA2AEndpoints(signal?: AbortSignal): Promise<A2AEndpointsResponse> {
  return api<A2AEndpointsResponse>("/remote-trigger/endpoints", { signal });
}

export interface A2AEvent {
  ts: number | null;
  event_type: string;
  severity: string;
  task_id: string | null;
  origin_id: string | null;
  endpoint_id: string | null;
  persona: string | null;
  engine_id: string | null;
  status: string | null;
  reason: string | null;
  duration_ms: number | null;
  nonce_prefix: string | null;
  ttl_s: number | null;
  sender_instance_id: string | null;
  instance_id_match: boolean | null;
  filter_pass_count: number | null;
  filter_reject_count: number | null;
}

export interface A2ALogResponse {
  tenant_id: string;
  ts: number;
  count: number;
  chain_size_b?: number;
  events: A2AEvent[];
  by_peer: Record<string, A2AEvent[]>;
}

export async function getA2ALog(
  params: { limit?: number; origin_id?: string; endpoint_id?: string; severity?: string } = {},
  signal?: AbortSignal,
): Promise<A2ALogResponse> {
  const q = new URLSearchParams();
  if (params.limit) q.set("limit", String(params.limit));
  if (params.origin_id) q.set("origin_id", params.origin_id);
  if (params.endpoint_id) q.set("endpoint_id", params.endpoint_id);
  if (params.severity) q.set("severity", params.severity);
  const qs = q.toString();
  return api<A2ALogResponse>(`/remote-trigger/log${qs ? `?${qs}` : ""}`, { signal });
}

// ── A2A Pairing (invite-code flow) ────────────────────────────────────

export interface A2APairMyInfo {
  instance_id: string;
  label: string;
  tenant_id: string;
}

export interface A2AGenerateRequest {
  label: string;
  url: string;
  console_url: string;
  peer_origin_id: string;
  max_ttl_s?: number;
  ttl_minutes?: number;
}

export interface A2AGenerateResponse {
  invite_code: string;
  accept_id: string;
  expires_at: number;
  accept_url: string;
}

export interface A2ARedeemRequest {
  invite_code: string;
  our_url: string;
  our_console_url: string;
  our_label: string;
  our_origin_id: string;
  spawn_worker?: boolean;
}

export interface A2ARedeemResponse {
  ok: boolean;
  paired_with: string;
  issuer_label: string;
  issuer_instance_id: string;
  our_origin_id: string;
  bidirectional: boolean;
}

export async function getA2APairMyInfo(signal?: AbortSignal): Promise<A2APairMyInfo> {
  return api<A2APairMyInfo>("/remote-trigger/pair/my-info", { signal });
}

export async function generateA2AInvite(
  body: A2AGenerateRequest,
  csrf: string,
): Promise<A2AGenerateResponse> {
  return api<A2AGenerateResponse>("/remote-trigger/pair/generate", {
    method: "POST",
    body,
    csrf,
  });
}

export async function redeemA2AInvite(
  body: A2ARedeemRequest,
  csrf: string,
): Promise<A2ARedeemResponse> {
  return api<A2ARedeemResponse>("/remote-trigger/pair/redeem", {
    method: "POST",
    body,
    csrf,
  });
}

// ── A2A CLI-Token flow (ADR-0063) ──────────────────────────────────────

export interface CLIInviteRequest {
  url: string;
  origin_id: string;
  label?: string;
  scope?: string;
  ttl_hours?: number;
  single_use?: boolean;
  spawn_worker?: boolean;
  max_call_ttl_s?: number;
}

export interface CLIInviteResponse {
  token: string;
  ikey: string;
  oid: string;
  exp: number | null;
}

export interface CLIAcceptRequest {
  token: string;
  overwrite?: boolean;
}

export interface CLIAcceptResponse {
  ok: boolean;
  oid: string;
  url: string;
  personas: string[];
  spawn_worker: boolean;
  exp: number | null;
}

export interface InviteListEntry {
  ikey: string;
  oid: string;
  lbl: string;
  iat: number;
  exp: number | null;
  su: boolean;
  status: "pending" | "accepted" | "revoked" | "expired";
}

export interface InviteListResponse {
  invites: InviteListEntry[];
}

export async function generateCLIInvite(
  body: CLIInviteRequest,
  csrf: string,
): Promise<CLIInviteResponse> {
  return api<CLIInviteResponse>("/remote-trigger/pair/cli-invite", {
    method: "POST",
    body,
    csrf,
  });
}

export async function acceptCLIInvite(
  body: CLIAcceptRequest,
  csrf: string,
): Promise<CLIAcceptResponse> {
  return api<CLIAcceptResponse>("/remote-trigger/pair/cli-accept", {
    method: "POST",
    body,
    csrf,
  });
}

export async function listA2AInvites(signal?: AbortSignal): Promise<InviteListResponse> {
  return api<InviteListResponse>("/remote-trigger/pair/invites", { signal });
}

export async function revokeA2AInvite(ikey: string, csrf: string): Promise<void> {
  return api<void>(`/remote-trigger/pair/invites/${encodeURIComponent(ikey)}`, {
    method: "DELETE",
    csrf,
  });
}

// ── A2A Friendship Token (ADR-0070) ───────────────────────────────────────

export interface FriendshipCreateRequest {
  url?: string;
  label?: string;
  ttl_hours?: number;
  personas?: string;
  max_call_ttl_s?: number;
  remember_url?: boolean;
}

export interface FriendshipCreateResponse {
  token: string;
  kid: string;
  expires: number | null;
}

export interface FriendshipImportRequest {
  token: string;
  peer_url?: string;
  overwrite?: boolean;
  spawn_worker?: boolean;
}

export interface FriendshipImportResponse {
  ok: boolean;
  kid: string;
  state: "PENDING" | "ACTIVE";
  url: string | null;
  label: string | null;
  personas: string[];
  expires: number | null;
}

export interface FriendshipConnection {
  kid: string;
  state: "PENDING" | "ACTIVE";
  label: string | null;
  personas: string[];
  url: string | null;
  expires: number | null;
}

export interface FriendshipConnectionsResponse {
  connections: FriendshipConnection[];
  count: number;
}

export interface MyUrlResponse {
  url: string | null;
  suggested: string | null;
}

export async function getMyA2AUrl(signal?: AbortSignal): Promise<MyUrlResponse> {
  return api<MyUrlResponse>("/remote-trigger/pair/my-url", { signal });
}

export async function setMyA2AUrl(url: string, csrf: string): Promise<MyUrlResponse> {
  return api<MyUrlResponse>("/remote-trigger/pair/my-url", {
    method: "POST",
    body: { url },
    csrf,
  });
}

export async function createFriendshipToken(
  body: FriendshipCreateRequest,
  csrf: string,
): Promise<FriendshipCreateResponse> {
  return api<FriendshipCreateResponse>("/remote-trigger/pair/friendship/create", {
    method: "POST",
    body,
    csrf,
  });
}

export async function importFriendshipToken(
  body: FriendshipImportRequest,
  csrf: string,
): Promise<FriendshipImportResponse> {
  return api<FriendshipImportResponse>("/remote-trigger/pair/friendship/import", {
    method: "POST",
    body,
    csrf,
  });
}

export async function setFriendshipUrl(
  kid: string,
  peer_url: string,
  csrf: string,
): Promise<{ ok: boolean; kid: string; state: string }> {
  return api("/remote-trigger/pair/friendship/set-url", {
    method: "POST",
    body: { kid, peer_url },
    csrf,
  });
}

export async function revokeFriendshipToken(
  kid: string,
  csrf: string,
): Promise<{ ok: boolean; kid: string }> {
  return api(`/remote-trigger/pair/friendship/${encodeURIComponent(kid)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function listFriendshipConnections(
  signal?: AbortSignal,
): Promise<FriendshipConnectionsResponse> {
  return api<FriendshipConnectionsResponse>("/remote-trigger/pair/friendship/connections", {
    signal,
  });
}

// ── A2A Origin permission editing ──────────────────────────────────────

export interface OriginPatchRequest {
  spawn_worker?: boolean;
  enabled?: boolean;
  allowed_personas?: string[];
  max_ttl_s?: number | null;
}

export async function patchA2AOrigin(
  originId: string,
  body: OriginPatchRequest,
  csrf: string,
): Promise<{ ok: boolean; origin_id: string; spawn_worker: boolean; enabled: boolean; allowed_personas: string[]; max_ttl_s: number | null }> {
  return api(`/remote-trigger/origins/${encodeURIComponent(originId)}`, {
    method: "PATCH",
    body,
    csrf,
  });
}

export async function deleteA2AOrigin(
  originId: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/remote-trigger/origins/${encodeURIComponent(originId)}`, {
    method: "DELETE",
    csrf,
  });
}

export interface EndpointPatchRequest {
  label?: string | null;
  url?: string | null;
  enabled?: boolean;
  default_ttl_s?: number | null;
}

export async function patchA2AEndpoint(
  endpointId: string,
  body: EndpointPatchRequest,
  csrf: string,
): Promise<{ ok: boolean; endpoint_id: string; label: string | null; url: string | null; enabled: boolean; default_ttl_s: number | null }> {
  return api(`/remote-trigger/endpoints/${encodeURIComponent(endpointId)}`, {
    method: "PATCH",
    body,
    csrf,
  });
}

export async function deleteA2AEndpoint(
  endpointId: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/remote-trigger/endpoints/${encodeURIComponent(endpointId)}`, {
    method: "DELETE",
    csrf,
  });
}

// ── File Hub ───────────────────────────────────────────────────────────

export interface FileEntry {
  name: string;
  rel_path: string;
  is_dir: boolean;
  size_bytes: number;
  mtime: number | null;
  access: "full" | "read" | "none";
  children?: FileEntry[];
}

export interface FileTreeResponse {
  tenant_id: string;
  root: string;
  path: string;
  is_dir: boolean;
  size_bytes: number;
  mtime: number | null;
  access: "full" | "read" | "none";
  ts: number;
  children: FileEntry[];
  quota: {
    used_bytes: number;
    limit_bytes: number;
    used_pct: number;
  };
}

export async function listFilesTree(
  path = "",
  depth = 2,
  signal?: AbortSignal,
): Promise<FileTreeResponse> {
  const q = new URLSearchParams({ depth: String(depth) });
  if (path) q.set("path", path);
  return api<FileTreeResponse>(`/files/tree?${q}`, { signal });
}

export interface FileContentResponse {
  path: string;
  name: string;
  size_bytes: number;
  mtime: number | null;
  mime: string;
  kind: "text" | "image" | "binary";
  content?: string | null;
  content_b64?: string;
  truncated?: boolean;
}

export async function getFileContent(
  path: string,
  signal?: AbortSignal,
): Promise<FileContentResponse> {
  return api<FileContentResponse>(
    `/files/content?path=${encodeURIComponent(path)}`,
    { signal },
  );
}

export function fileDownloadUrl(path: string): string {
  return `/v1/console/files/download?path=${encodeURIComponent(path)}`;
}

export async function uploadFile(
  dir: string,
  file: File,
  csrf: string,
): Promise<{ ok: true; path: string; name: string; size_bytes: number }> {
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await fetch(
    `/v1/console/files/upload?dir=${encodeURIComponent(dir)}`,
    {
      method: "POST",
      credentials: "include",
      headers: { "X-CSRF-Token": csrf },
      body: form,
    },
  );
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text);
  }
  return res.json();
}

export async function deleteFile(
  path: string,
  csrf: string,
): Promise<{ ok: true; path: string; deleted: true }> {
  return api(`/files?path=${encodeURIComponent(path)}`, { method: "DELETE", csrf });
}

export async function createDir(
  path: string,
  csrf: string,
): Promise<{ ok: true; path: string }> {
  return api("/files/mkdir", { method: "POST", csrf, body: { path } });
}

// ── CorvinSpace (Layer 40) ─────────────────────────────────────────

export interface SpaceProfile {
  display_name: string;
  bio: string;
  contact_handle: string;
  website: string;
  location: string;
  created_at: number;
  updated_at: number;
}

export interface SpaceDomain {
  slug: string;
  name: string;
  description: string;
  visibility: "public" | "followers" | "private";
  created_at: number;
  updated_at: number;
  post_count: number;
}

export interface SocialStatus {
  tenant_id: string;
  status: {
    is_enabled: boolean;
    consented_at: number | null;
    actor_id: string | null;
  } | null;
  follower_count: number | null;
  following_count: number | null;
  ts: number;
}

export interface SocialActor {
  actor_id: string;
  display_name: string | null;
  inbox_url: string;
  compliance_zone: string | null;
  is_ai: boolean;
  relationship: string;
}

export async function getSpaceProfile(signal?: AbortSignal) {
  return api<{ profile: SpaceProfile | null; social_actor_id: string | null; tenant_id: string }>("/space/profile", { signal });
}

export async function updateSpaceProfile(csrf: string, data: Partial<SpaceProfile>, signal?: AbortSignal) {
  return api<{ profile: SpaceProfile }>("/space/profile", { method: "PUT", csrf, body: data, signal });
}

export async function getSpaceDomains(signal?: AbortSignal) {
  return api<{ domains: SpaceDomain[]; max_domains: number; license_unlimited: boolean }>("/space/domains", { signal });
}

export async function createSpaceDomain(csrf: string, data: { slug: string; name: string; description?: string; visibility?: string }, signal?: AbortSignal) {
  return api<SpaceDomain>("/space/domains", { method: "POST", csrf, body: data, signal });
}

export async function deleteSpaceDomain(csrf: string, slug: string, signal?: AbortSignal) {
  return api<{ ok: boolean }>(`/space/domains/${slug}`, { method: "DELETE", csrf, signal });
}

export async function publishToDomain(csrf: string, slug: string, data: { content: string; tags?: string[]; visibility?: string }, signal?: AbortSignal) {
  return api<{ ok: boolean; post_id: string }>(`/space/domains/${slug}/publish`, { method: "POST", csrf, body: data, signal });
}

export async function getSocialStatus(signal?: AbortSignal) {
  return api<SocialStatus>("/space/social/status", { signal });
}

export async function joinSocial(csrf: string, data: { display_name: string; host: string; compliance_zone: string }, signal?: AbortSignal) {
  return api<{ status: string; actor_id?: string }>("/space/social/join", { method: "POST", csrf, body: data, signal });
}

export async function leaveSocial(csrf: string, signal?: AbortSignal) {
  return api<{ status: string }>("/space/social/leave", { method: "POST", csrf, signal });
}

export async function followActor(csrf: string, data: { actor_id: string; inbox_url: string; public_key_hex: string; display_name?: string; compliance_zone?: string }, signal?: AbortSignal) {
  return api<{ ok: boolean }>("/space/social/follow", { method: "POST", csrf, body: data, signal });
}

export async function getSocialFollowing(signal?: AbortSignal) {
  return api<{ actors: SocialActor[] }>("/space/social/following", { signal });
}

export async function getSocialFollowers(signal?: AbortSignal) {
  return api<{ actors: SocialActor[] }>("/space/social/followers", { signal });
}

// ── Social Capability Grants (Layer 41) ────────────────────────────────

export interface Grant {
  grant_id: string;
  grantee_actor: string;
  grantor_actor: string;
  capabilities: string[];
  conditions: Record<string, unknown>;
  issued_at: number | null;
  revoked_at: number | null;
}

export interface GrantTemplate {
  id: string;
  label: string;
  description: string;
  capabilities: string[];
  conditions: Record<string, unknown>;
  requires_confirmation?: boolean;
}

export interface GrantListResponse {
  local_actor_id: string;
  grants: Grant[];
  ts: number;
}

export interface GrantTemplatesResponse {
  templates: GrantTemplate[];
}

export async function listGrantTemplates(signal?: AbortSignal): Promise<GrantTemplatesResponse> {
  return api<GrantTemplatesResponse>("/grants/templates", { signal });
}

export async function listGrants(
  params: { grantee_actor?: string; include_revoked?: boolean } = {},
  signal?: AbortSignal,
): Promise<GrantListResponse> {
  const q = new URLSearchParams();
  if (params.grantee_actor) q.set("grantee_actor", params.grantee_actor);
  if (params.include_revoked) q.set("include_revoked", "true");
  const qs = q.toString();
  return api<GrantListResponse>(`/grants${qs ? `?${qs}` : ""}`, { signal });
}

export async function createGrant(
  body: { grantee_actor: string; capabilities: string[]; conditions?: Record<string, unknown> },
  csrf: string,
): Promise<{ ok: true; grant: Grant; ts: number }> {
  return api("/grants", { method: "POST", csrf, body });
}

export async function revokeGrant(
  grant_id: string,
  csrf: string,
): Promise<{ ok: true; ts: number }> {
  return api(`/grants/${encodeURIComponent(grant_id)}`, { method: "DELETE", csrf });
}

// ── CorvinOrg — Organisations (Layer 42) ──────────────────────────────

export interface OrgSummary {
  handle: string;
  actor_id: string;
  display_name: string;
  summary: string;
  verified_domain: string | null;
  member_count: number;
  agent_count: number;
}

export interface OrgMember {
  actor_id: string;
  role: "owner" | "admin" | "editor" | "agent";
  added_at?: number | null;
}

export interface OrgEndorsement {
  endorsement_id: string;
  agent_actor_id: string;
  org_actor_id: string;
  scope: string[];
  issued_at: number | null;
  expires_at: number | null;
  revoked_at: number | null;
}

export interface OrgDetail {
  handle: string;
  actor: {
    id: string | null;
    display_name: string | null;
    summary: string | null;
    public_key_hex: string;
    verified_domain: string | null;
    affiliated_actors: string[];
    created_at: number | null;
  };
  config: {
    responsible_party: string | null;
    policy: Record<string, unknown>;
  };
  members: OrgMember[];
  agents: OrgEndorsement[];
  grants: Grant[];
  ts: number;
}

export interface OrgListResponse {
  orgs: OrgSummary[];
  ts: number;
}

export async function listOrgs(signal?: AbortSignal): Promise<OrgListResponse> {
  return api<OrgListResponse>("/orgs", { signal });
}

export async function createOrg(
  body: { handle: string; display_name: string; summary?: string; host?: string },
  csrf: string,
): Promise<{ ok: true; org: OrgSummary; ts: number }> {
  return api("/orgs", { method: "POST", csrf, body });
}

export async function getOrg(handle: string, signal?: AbortSignal): Promise<OrgDetail> {
  return api<OrgDetail>(`/orgs/${encodeURIComponent(handle)}`, { signal });
}

export async function dissolveOrg(
  handle: string,
  csrf: string,
): Promise<{ ok: true; ts: number }> {
  return api(`/orgs/${encodeURIComponent(handle)}`, { method: "DELETE", csrf });
}

export async function addOrgMember(
  handle: string,
  body: { actor_id: string; role: "owner" | "admin" | "editor" | "agent" },
  csrf: string,
): Promise<{ ok: true; members: OrgMember[]; ts: number }> {
  return api(`/orgs/${encodeURIComponent(handle)}/members`, { method: "POST", csrf, body });
}

export async function removeOrgMember(
  handle: string,
  actor_id: string,
  csrf: string,
): Promise<{ ok: true; members: OrgMember[]; ts: number }> {
  return api(
    `/orgs/${encodeURIComponent(handle)}/members?actor_id=${encodeURIComponent(actor_id)}`,
    { method: "DELETE", csrf },
  );
}

export async function updateOrgMemberRole(
  handle: string,
  actor_id: string,
  role: "owner" | "admin" | "editor" | "agent",
  csrf: string,
): Promise<{ ok: true; members: OrgMember[]; ts: number }> {
  return api(`/orgs/${encodeURIComponent(handle)}/members`, {
    method: "PATCH",
    csrf,
    body: { actor_id, role },
  });
}

export async function affiliateOrgAgent(
  handle: string,
  body: { agent_actor_id: string; scope?: string[]; ttl_days?: number },
  csrf: string,
): Promise<{ ok: true; endorsement: OrgEndorsement; ts: number }> {
  return api(`/orgs/${encodeURIComponent(handle)}/agents`, { method: "POST", csrf, body });
}

export async function deaffiliateOrgAgent(
  handle: string,
  endorsement_id: string,
  csrf: string,
): Promise<{ ok: true; ts: number }> {
  return api(
    `/orgs/${encodeURIComponent(handle)}/agents/${encodeURIComponent(endorsement_id)}`,
    { method: "DELETE", csrf },
  );
}

export async function listOrgGrants(
  handle: string,
  include_revoked = false,
  signal?: AbortSignal,
): Promise<{ grants: Grant[]; ts: number }> {
  const q = include_revoked ? "?include_revoked=true" : "";
  return api(`/orgs/${encodeURIComponent(handle)}/grants${q}`, { signal });
}

export async function createOrgGrant(
  handle: string,
  body: { grantee_actor: string; capabilities: string[]; conditions?: Record<string, unknown> },
  csrf: string,
): Promise<{ ok: true; grant: Grant; ts: number }> {
  return api(`/orgs/${encodeURIComponent(handle)}/grants`, { method: "POST", csrf, body });
}

export async function revokeOrgGrant(
  handle: string,
  grant_id: string,
  csrf: string,
): Promise<{ ok: true; ts: number }> {
  return api(
    `/orgs/${encodeURIComponent(handle)}/grants/${encodeURIComponent(grant_id)}`,
    { method: "DELETE", csrf },
  );
}

export interface OrgNetworkNode {
  id: string;
  label: string;
  type: "human" | "agent" | "org";
  role?: string | null;
}

export interface OrgNetworkEdge {
  from: string;
  to: string;
  type: "member" | "agent" | "grant";
  caps: string[];
}

export async function getOrgNetwork(
  handle: string,
  signal?: AbortSignal,
): Promise<{ org_handle: string; nodes: OrgNetworkNode[]; edges: OrgNetworkEdge[] }> {
  return api(`/orgs/${encodeURIComponent(handle)}/network`, { signal });
}

// ── ADR-0062: Setup Gate ────────────────────────────────────────────

export interface SetupStatus {
  first_run: boolean;
  engine_connected: boolean;
  claude_cli_ok: boolean;
  anthropic_key_set: boolean;
  bridges_configured: string[];
  setup_complete: boolean;
}

export async function getSetupStatus(signal?: AbortSignal): Promise<SetupStatus> {
  return api("/setup/status", { signal });
}

export async function postSetupComplete(csrf: string): Promise<{ ok: boolean }> {
  return api("/setup/complete", { method: "POST", csrf });
}

export async function postTestEngine(
  engine_id: string,
  csrf: string,
  signal?: AbortSignal,
): Promise<{ ok: boolean; detail: string; steps?: string[]; platform?: string; download_url?: string }> {
  return api("/setup/test-engine", { method: "POST", body: { engine_id }, csrf, signal });
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

// ── ADR-0062: Console Assistant ─────────────────────────────────────

export interface AssistantHistoryEntry {
  role: "user" | "assistant";
  content: string;
}

export interface AssistantContext {
  current_page?: string;
  setup_status?: Partial<SetupStatus>;
  license_tier?: string;
  personas?: string[];
  language?: string;   // detected UI language, e.g. "de" | "en"
}

export async function postAssistantMessage(
  message: string,
  context: AssistantContext,
  csrf: string,
  history?: AssistantHistoryEntry[],
): Promise<{ ok: boolean; response: string }> {
  return api("/assistant/message", {
    method: "POST",
    body: { message, context, history: history ?? [] },
    csrf,
  });
}

export async function getAssistantPing(signal?: AbortSignal): Promise<{ available: boolean; version: string | null }> {
  return api("/assistant/ping", { signal });
}

// ── Memory API ────────────────────────────────────────────────────────────

export interface MemoryFileSummary {
  name: string;
  type: "index" | "user" | "feedback" | "project" | "reference" | "other";
  size_bytes: number | null;
  modified: number | null;
  description: string | null;
}

export interface MemoryIndex {
  tenant_id: string;
  memory_dir: string;
  present: boolean;
  ts?: number;
  count: number;
  files: MemoryFileSummary[];
}

export interface MemoryFileDetail {
  name: string;
  type: string;
  path: string;
  size_bytes: number;
  modified: number;
  body: string;
}

export function getMemoryIndex(signal?: AbortSignal): Promise<MemoryIndex> {
  return api("/memory", { signal });
}

export function getMemoryFile(name: string, signal?: AbortSignal): Promise<MemoryFileDetail> {
  return api(`/memory/${encodeURIComponent(name)}`, { signal });
}

export function putMemoryFile(
  name: string,
  body: string,
  csrf: string,
): Promise<{ name: string; size_bytes: number; modified: number; ok: boolean }> {
  return api(`/memory/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: { body, re_auth_token: null },
    csrf,
  });
}

export function deleteMemoryFile(
  name: string,
  csrf: string,
): Promise<{ name: string; found: boolean; ok: boolean }> {
  return api(`/memory/${encodeURIComponent(name)}`, {
    method: "DELETE",
    body: { re_auth_token: null },
    csrf,
  });
}

export async function ttsBlob(text: string, lang: string, csrf: string): Promise<Blob> {
  const res = await fetch("/v1/console/voice/tts", {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
    },
    body: JSON.stringify({ text, lang }),
  });
  if (res.status === 204) return new Blob();
  if (!res.ok) {
    const errText = await res.text();
    throw new ApiError(res.status, errText);
  }
  return res.blob();
}

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

// ── ADR-0096 M3 — MCP Plugin Manager ──────────────────────────────

export interface McpToolSecret {
  name: string;
  required: boolean;
}

export interface McpToolSummary {
  id: string;
  source: string;
  installed_at: string | null;
  runtime: { command: string; args: string[] } | null;
  compliance: { locality?: string; network_egress?: string };
  secrets: McpToolSecret[];
  active: boolean;
  active_scopes: string[];
  sha256?: string | null;
}

export interface McpToolListResponse {
  tenant_id: string;
  count: number;
  tools: McpToolSummary[];
  active: Record<string, string[]>;
}

export interface McpToolResponse {
  ok: boolean;
  tool: McpToolSummary;
}

export async function listMcpPlugins(signal?: AbortSignal): Promise<McpToolListResponse> {
  return api<McpToolListResponse>("/mcp-plugins", { signal });
}

export async function installMcpPlugin(
  source: string,
  csrf: string,
  allow_unpin = false,
): Promise<McpToolResponse> {
  return api<McpToolResponse>("/mcp-plugins/install", {
    method: "POST",
    csrf,
    body: { source, allow_unpin },
  });
}

export async function activateMcpPlugin(
  toolId: string,
  scope: string,
  csrf: string,
): Promise<McpToolResponse> {
  return api<McpToolResponse>(`/mcp-plugins/${encodeURIComponent(toolId)}/activate`, {
    method: "POST",
    csrf,
    body: { scope },
  });
}

export async function deactivateMcpPlugin(
  toolId: string,
  scope: string,
  csrf: string,
): Promise<McpToolResponse> {
  return api<McpToolResponse>(`/mcp-plugins/${encodeURIComponent(toolId)}/deactivate`, {
    method: "POST",
    csrf,
    body: { scope },
  });
}

export async function removeMcpPlugin(
  toolId: string,
  csrf: string,
): Promise<{ ok: boolean; tool_id: string }> {
  return api(`/mcp-plugins/${encodeURIComponent(toolId)}`, {
    method: "DELETE",
    csrf,
  });
}

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

// ── ADR-0106 DSI v1 — Data Sources ────────────────────────────────────────

export interface DSIAdapterMeta {
  adapter_name: string;
  display_name: string;
  description: string;
  supported_formats: string[];
  locality: string;
  network_egress: string;
  config_schema: Record<string, unknown>;
  dsi_version: string;
  tier: string;
}

export interface DSIConnection {
  dsi_version: string;
  name: string;
  adapter: string;
  config: Record<string, unknown>;
  data_classification: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "SECRET";
  secrets?: string[];
  data_residency: string;
  tags?: string[];
  pii_scan?: boolean;
  read_only?: boolean;
  auto_refresh_schema?: boolean;
  description?: string;
  adapter_meta?: DSIAdapterMeta | null;
}

export interface DSIPingResult {
  ok: boolean;
  latency_ms: number;
  detail: string;
}

export async function listDataSources(signal?: AbortSignal): Promise<DSIConnection[]> {
  return api<DSIConnection[]>("/data-sources", { signal });
}

export async function listDataSourceAdapters(signal?: AbortSignal): Promise<DSIAdapterMeta[]> {
  return api<DSIAdapterMeta[]>("/data-sources/adapters", { signal });
}

export async function getDataSource(name: string, signal?: AbortSignal): Promise<DSIConnection> {
  return api<DSIConnection>(`/data-sources/${encodeURIComponent(name)}`, { signal });
}

export async function registerDataSource(
  manifest: Record<string, unknown>,
  csrf: string,
): Promise<DSIConnection> {
  return api<DSIConnection>("/data-sources", {
    method: "POST",
    body: { manifest },
    csrf,
  });
}

export async function testDataSource(
  name: string,
  csrf: string,
): Promise<DSIPingResult> {
  return api<DSIPingResult>(`/data-sources/${encodeURIComponent(name)}/test`, {
    method: "POST",
    body: {},
    csrf,
  });
}

export async function unregisterDataSource(
  name: string,
  csrf: string,
): Promise<void> {
  return api<void>(`/data-sources/${encodeURIComponent(name)}`, {
    method: "DELETE",
    csrf,
  });
}

export interface DSIAuditEvent {
  event_type: string;
  severity: string;
  ts: number | null;
  details: Record<string, unknown>;
}

export async function getDataSourceAudit(
  name: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<DSIAuditEvent[]> {
  return api<DSIAuditEvent[]>(
    `/data-sources/${encodeURIComponent(name)}/audit?limit=${limit}`,
    { signal },
  );
}

// ── ADR-0142: Layer Extensions ─────────────────────────────────────────────

export interface CoreLayer {
  name: string;
  version: string;
  active: boolean;
  core: true;
  description: string;
}

export interface ExtensionHookDecl {
  event: string;
  script: string;
  priority: number;
}

export interface ExtensionManifest {
  name: string;
  version: string;
  description: string;
  author: string;
  license: string;
  scope: string;
  hooks: ExtensionHookDecl[];
  provides: { name: string; version?: string }[];
  requires: string[];
  mcp_tools: unknown[];
  enabled: boolean;
}

export interface ExtensionList {
  core: CoreLayer[];
  extensions: ExtensionManifest[];
}

export interface ExtensionValidateResult {
  ok: boolean;
  name?: string;
  version?: string;
  scope?: string;
  hooks?: number;
  requires?: number;
  error?: string;
}

export async function listExtensions(signal?: AbortSignal): Promise<ExtensionList> {
  return api<ExtensionList>("/extensions", { signal });
}

export async function getExtension(
  name: string,
  signal?: AbortSignal,
): Promise<ExtensionManifest & { core?: boolean; removable?: boolean }> {
  return api(`/extensions/${encodeURIComponent(name)}`, { signal });
}

export async function installExtension(
  source: string,
  csrf: string,
  opts: { scope?: string; enable?: boolean } = {},
): Promise<{ name: string; version: string; scope: string; enabled: boolean }> {
  return api("/extensions", {
    method: "POST",
    body: { source, scope: opts.scope ?? null, enable: opts.enable ?? false },
    csrf,
  });
}

export async function setExtensionEnabled(
  name: string,
  enabled: boolean,
  csrf: string,
): Promise<{ name: string; version: string; scope: string; enabled: boolean }> {
  return api(`/extensions/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: { enabled },
    csrf,
  });
}

export async function removeExtension(name: string, csrf: string): Promise<void> {
  return api<void>(`/extensions/${encodeURIComponent(name)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function validateExtensionManifest(
  manifestYaml: string,
  signal?: AbortSignal,
): Promise<ExtensionValidateResult> {
  return api<ExtensionValidateResult>("/extensions/validate", {
    method: "POST",
    body: { manifest_yaml: manifestYaml },
    signal,
  });
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

// ── ADR-0124 M1: Custom Engine Registry ──────────────────────────────────────

export interface CustomEngineModel {
  id: string;
  context_length: number;
}

export interface CustomEngineManifest {
  engine_id: string;
  display_name: string;
  transport: "openai_compat" | "anthropic" | "ollama";
  base_url_hash: string;
  auth_env: string | null;
  locality: "local" | "eu_cloud" | "us_cloud";
  network_egress: "none" | "restricted" | "full";
  models: CustomEngineModel[];
  data_classification: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL";
  created_at: number;
  updated_at: number;
}

export interface CustomEngineListResponse {
  tenant_id: string;
  count: number;
  engines: CustomEngineManifest[];
}

export async function listCustomEngines(signal?: AbortSignal): Promise<CustomEngineListResponse> {
  return api<CustomEngineListResponse>("/engines/custom", { signal });
}

export interface CustomEngineRegisterRequest {
  display_name: string;
  transport: string;
  base_url: string;
  auth_env?: string | null;
  locality?: string;
  network_egress?: string;
  models?: CustomEngineModel[];
  data_classification?: string;
}

export async function registerCustomEngine(
  engine_id: string,
  body: CustomEngineRegisterRequest,
  csrf: string,
): Promise<{ ok: boolean; engine_id: string; updated: boolean }> {
  return api(`/engines/custom/${encodeURIComponent(engine_id)}`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function removeCustomEngine(
  engine_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/engines/custom/${encodeURIComponent(engine_id)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function pingCustomEngine(
  engine_id: string,
  csrf: string,
): Promise<{ ok: boolean; reachable: boolean; model_count?: number; error?: string }> {
  return api(`/engines/custom/${encodeURIComponent(engine_id)}/ping`, {
    method: "POST",
    csrf,
  });
}

// ── ADR-0124 M2: Custom Connector Registry ────────────────────────────────────

export interface CustomConnectorManifest {
  connector_id: string;
  display_name: string;
  transport: "stdio" | "sse" | "http";
  command?: string[];
  url?: string;
  env_secrets: string[];
  capabilities: string[];
  locality: string;
  network_egress: string;
  description: string;
  created_at: number;
  updated_at: number;
}

export interface CustomConnectorListResponse {
  tenant_id: string;
  count: number;
  connectors: CustomConnectorManifest[];
}

export async function listCustomConnectors(signal?: AbortSignal): Promise<CustomConnectorListResponse> {
  return api<CustomConnectorListResponse>("/connectors/custom", { signal });
}

export interface CustomConnectorRegisterRequest {
  display_name: string;
  transport: string;
  command?: string[] | null;
  url?: string | null;
  env_secrets?: string[];
  capabilities?: string[];
  locality?: string;
  network_egress?: string;
  description?: string;
}

export async function registerCustomConnector(
  connector_id: string,
  body: CustomConnectorRegisterRequest,
  csrf: string,
): Promise<{ ok: boolean; connector_id: string; updated: boolean }> {
  return api(`/connectors/custom/${encodeURIComponent(connector_id)}`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function removeCustomConnector(
  connector_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/connectors/custom/${encodeURIComponent(connector_id)}`, {
    method: "DELETE",
    csrf,
  });
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

// ── ADR-0124 M4: DSI v2 HTTP Adapter ─────────────────────────────────────────

export interface HttpAdapter {
  adapter_id: string;
  display_name: string;
  base_url_hash: string;
  auth_type: "none" | "bearer" | "api_key";
  auth_env: string | null;
  locality: string;
  network_egress: string;
  description: string;
  protocol: string;
  created_at: number;
  updated_at: number;
}

export interface HttpAdapterListResponse {
  tenant_id: string;
  count: number;
  adapters: HttpAdapter[];
}

export async function listHttpAdapters(signal?: AbortSignal): Promise<HttpAdapterListResponse> {
  return api<HttpAdapterListResponse>("/data-sources/adapters/http", { signal });
}

export interface HttpAdapterRegisterRequest {
  display_name: string;
  base_url: string;
  auth_type?: string;
  auth_env?: string | null;
  auth_header?: string | null;
  locality?: string;
  network_egress?: string;
  description?: string;
}

export async function registerHttpAdapter(
  adapter_id: string,
  body: HttpAdapterRegisterRequest,
  csrf: string,
): Promise<{ ok: boolean; adapter_id: string; updated: boolean }> {
  return api(`/data-sources/adapters/http/${encodeURIComponent(adapter_id)}`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function removeHttpAdapter(
  adapter_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/data-sources/adapters/http/${encodeURIComponent(adapter_id)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function pingHttpAdapter(
  adapter_id: string,
  csrf: string,
): Promise<{ ok: boolean; reachable: boolean; name?: string; version?: string; error?: string }> {
  return api(`/data-sources/adapters/http/${encodeURIComponent(adapter_id)}/ping`, {
    method: "POST",
    csrf,
  });
}

// ── ADR-0124 M5: Manual Skills ────────────────────────────────────────────────

export interface ManualSkill {
  name: string;
  scope: "user";
  origin: "manual";
  created_at: number | null;
  updated_at: number | null;
  sha256: string;
}

export interface ManualSkillListResponse {
  tenant_id: string;
  count: number;
  skills: ManualSkill[];
}

export async function listManualSkills(signal?: AbortSignal): Promise<ManualSkillListResponse> {
  return api<ManualSkillListResponse>("/skills/manual", { signal });
}

export async function createManualSkill(
  name: string,
  body_md: string,
  csrf: string,
): Promise<{ ok: boolean; name: string }> {
  return api("/skills/manual", { method: "POST", body: { name, body: body_md }, csrf });
}

export async function updateManualSkill(
  name: string,
  body_md: string,
  csrf: string,
): Promise<{ ok: boolean; name: string }> {
  return api(`/skills/manual/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: { body: body_md },
    csrf,
  });
}

export async function deleteManualSkill(
  name: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/skills/manual/${encodeURIComponent(name)}`, { method: "DELETE", csrf });
}

// ── ADR-0124 M5b: Manual Tools ────────────────────────────────────────────────

export interface ManualTool {
  name: string;
  description: string;
  origin: "manual";
  sha256: string;
  runtime: "python";
  scope: "user";
  created_at: number;
  updated_at: number;
}

export interface ManualToolListResponse {
  tenant_id: string;
  count: number;
  tools: ManualTool[];
}

export async function listManualTools(signal?: AbortSignal): Promise<ManualToolListResponse> {
  return api<ManualToolListResponse>("/tools/manual", { signal });
}

export async function createManualTool(
  name: string,
  description: string,
  impl: string,
  csrf: string,
): Promise<{ ok: boolean; name: string }> {
  return api("/tools/manual", {
    method: "POST",
    body: { name, description, impl, input_schema: {} },
    csrf,
  });
}

export async function deleteManualTool(
  name: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/tools/manual/${encodeURIComponent(name)}`, { method: "DELETE", csrf });
}

export async function previewManualTool(
  name: string,
  inputs: Record<string, unknown>,
  csrf: string,
): Promise<{ ok: boolean; exit_code: number; stdout: string; stderr: string }> {
  return api("/tools/preview", { method: "POST", body: { name, inputs }, csrf });
}

// ── ADR-0124 M6: Custom Audit Layers ─────────────────────────────────────────

export interface AuditLayer {
  layer_id: string;
  display_name: string;
  event_types: string[];
  allowed_fields: string[];
  description: string;
  created_at: number;
  updated_at: number;
}

export interface AuditLayerListResponse {
  tenant_id: string;
  count: number;
  layers: AuditLayer[];
}

export async function listAuditLayers(signal?: AbortSignal): Promise<AuditLayerListResponse> {
  return api<AuditLayerListResponse>("/audit/layers", { signal });
}

export interface AuditLayerRegisterRequest {
  display_name: string;
  event_types: string[];
  allowed_fields?: string[];
  description?: string;
}

export async function registerAuditLayer(
  layer_id: string,
  body: AuditLayerRegisterRequest,
  csrf: string,
): Promise<{ ok: boolean; layer_id: string; updated: boolean }> {
  return api(`/audit/layers/${encodeURIComponent(layer_id)}`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function removeAuditLayer(
  layer_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/audit/layers/${encodeURIComponent(layer_id)}`, { method: "DELETE", csrf });
}

export async function emitCustomAuditEvent(
  layer_id: string,
  event_type: string,
  details: Record<string, unknown>,
  csrf: string,
): Promise<{ ok: boolean; ts: number }> {
  return api("/audit/emit", {
    method: "POST",
    body: { layer_id, event_type, details },
    csrf,
  });
}

// ── ADR-0124 M7: Webhook Bridge ───────────────────────────────────────────────

export interface WebhookChannel {
  channel_id: string;
  display_name: string;
  hmac_secret_env: string | null;
  persona: string;
  rate_limit_per_hour: number;
  description: string;
  inbound_url: string;
  created_at: number;
  updated_at: number;
}

export interface WebhookChannelListResponse {
  tenant_id: string;
  count: number;
  channels: WebhookChannel[];
}

export async function listWebhookChannels(signal?: AbortSignal): Promise<WebhookChannelListResponse> {
  return api<WebhookChannelListResponse>("/bridges/custom", { signal });
}

export interface WebhookChannelRegisterRequest {
  display_name: string;
  hmac_secret_env?: string | null;
  persona?: string;
  rate_limit_per_hour?: number;
  description?: string;
}

export async function registerWebhookChannel(
  channel_id: string,
  body: WebhookChannelRegisterRequest,
  csrf: string,
): Promise<{ ok: boolean; channel_id: string; inbound_url: string; updated: boolean }> {
  return api(`/bridges/custom/${encodeURIComponent(channel_id)}`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function removeWebhookChannel(
  channel_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/bridges/custom/${encodeURIComponent(channel_id)}`, {
    method: "DELETE",
    csrf,
  });
}

// ── ADR-0131: Agent Lifecycle Governance ─────────────────────────────────────

export type AgentStatus =
  | "active"
  | "review_pending"
  | "review_overdue"
  | "pending_sunset"
  | "disabled"
  | "orphan";

export interface AgentSignOff {
  role: "it" | "business" | "compliance";
  signer: string;
  signed_at: string;
}

export interface AgentCharter {
  agent_id: string;
  name: string;
  kind: "forge_tool" | "skill";
  scope: "project" | "user" | "tenant_wide";
  status: AgentStatus;
  it_owner: string;
  business_owner: string;
  compliance_owner: string;
  problem: string;
  success_metric: string;
  baseline: number;
  target: number;
  unit: string;
  created_at: string;
  review_date: string;
  sunset_date: string;
  data_class: string;
  egress_zone: string;
  engine_allowlist: string[];
  sign_offs: AgentSignOff[];
  signed_scope: string | null;
  required_roles: string[];
  days_to_review: number;
  days_to_sunset: number;
  disabled: boolean;
  version: number;
}

export interface CreateAgentCharterRequest {
  agent_id: string;
  name: string;
  kind: "forge_tool" | "skill";
  scope: "project" | "user" | "tenant_wide";
  problem: string;
  success_metric: string;
  baseline: number;
  target: number;
  unit: string;
  it_owner: string;
  business_owner: string;
  compliance_owner: string;
  review_date: string;
  sunset_date: string;
  data_class: string;
  egress_zone: string;
  engine_allowlist?: string[];
}

export interface SignOffRequest {
  scope_target: "project" | "user" | "tenant_wide";
  role: "it" | "business" | "compliance";
}

export async function listAgents(signal?: AbortSignal): Promise<AgentCharter[]> {
  return api<AgentCharter[]>("/agents", { signal });
}

export async function getAgent(agentId: string, signal?: AbortSignal): Promise<AgentCharter> {
  return api<AgentCharter>(`/agents/${encodeURIComponent(agentId)}`, { signal });
}

export async function createAgentCharter(
  body: CreateAgentCharterRequest,
  csrf: string,
): Promise<AgentCharter> {
  return api<AgentCharter>("/agents", { method: "POST", body, csrf });
}

export async function addAgentSignOff(
  agentId: string,
  body: SignOffRequest,
  csrf: string,
): Promise<AgentCharter> {
  return api<AgentCharter>(`/agents/${encodeURIComponent(agentId)}/sign`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function revokeAgentSignOff(
  agentId: string,
  role: string,
  csrf: string,
): Promise<AgentCharter> {
  return api<AgentCharter>(`/agents/${encodeURIComponent(agentId)}/sign/${encodeURIComponent(role)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function disableAgent(agentId: string, csrf: string): Promise<AgentCharter> {
  return api<AgentCharter>(`/agents/${encodeURIComponent(agentId)}/disable`, {
    method: "POST",
    csrf,
  });
}

// ── Universal Activity Hub (UAH) ─────────────────────────────────────────────

export interface ActivityEntry {
  ts: number;
  action: string;
  panel: string;
  entity_id: string;
  chat_key: string;
  summary: string;
  extra?: Record<string, string>;
  panel_label: string;
  action_label: string;
}

export interface ActivityFeedResponse {
  items: ActivityEntry[];
  returned: number;
}

export async function getActivityFeed(
  opts: { limit?: number; panel?: string; chat_key?: string } = {},
  signal?: AbortSignal,
): Promise<ActivityFeedResponse> {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.panel) params.set("panel", opts.panel);
  if (opts.chat_key) params.set("chat_key", opts.chat_key);
  const qs = params.toString();
  return api<ActivityFeedResponse>(`/activity/feed${qs ? "?" + qs : ""}`, { signal });
}

// ── Chat Attachments ───────────────────────────────────────────────

export interface AttachmentMeta {
  name: string;
  size: number;
  mime: string;
  path: string; // relative to workdir, e.g. "attachments/report.csv"
}

export async function uploadAttachments(
  sid: string,
  files: File[],
  csrf: string,
): Promise<AttachmentMeta[]> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f, f.name);
  }
  const res = await fetch(`${BASE}/chat/sessions/${encodeURIComponent(sid)}/attachments`, {
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
      if (json?.detail) detail = String(json.detail);
    } catch { /* keep text */ }
    throw new ApiError(res.status, detail);
  }
  const data = await res.json() as { attachments: AttachmentMeta[] };
  return data.attachments;
}

// ── ULO (ADR-0163 M4) — User-Defined Learning Objectives ─────────────────

export interface UloObjective {
  id:                      string;
  text:                    string;
  priority:                "low" | "medium" | "high";
  scope:                   "session" | "chat" | "all";
  active:                  boolean;
  created_at:              number;
  updated_at:              number;
  compliance_window:       number;
  compliance_rate:         number | null;
  reinforcement_threshold: number;
  turns_checked:           number;
  consecutive_failures:    number;
  check_trigger:           "always" | "code" | "review" | "commit";
}

export interface UloListResponse {
  objectives:   UloObjective[];
  count:        number;
  active_count: number;
}

export function getUloObjectives(
  channel: string,
  chat: string,
  signal?: AbortSignal,
): Promise<UloListResponse> {
  return api(`/ulo/objectives?channel=${encodeURIComponent(channel)}&chat=${encodeURIComponent(chat)}`, { signal });
}

export function addUloObjective(
  channel: string,
  chat_key: string,
  text: string,
  priority: "low" | "medium" | "high",
  csrf: string,
): Promise<{ objective: UloObjective }> {
  return api("/ulo/objectives", {
    method: "POST",
    body: { channel, chat_key, text, priority },
    csrf,
  });
}

export function pauseUloObjective(
  id: string,
  channel: string,
  chat_key: string,
  csrf: string,
): Promise<{ id: string; active: boolean }> {
  return api(`/ulo/objectives/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: { action: "pause", channel, chat_key },
    csrf,
  });
}

export function resumeUloObjective(
  id: string,
  channel: string,
  chat_key: string,
  csrf: string,
): Promise<{ id: string; active: boolean }> {
  return api(`/ulo/objectives/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: { action: "resume", channel, chat_key },
    csrf,
  });
}

export function deleteUloObjective(
  id: string,
  channel: string,
  chat_key: string,
  csrf: string,
): Promise<{ id: string; deleted: boolean }> {
  const params = new URLSearchParams({ channel, chat: chat_key });
  return api(`/ulo/objectives/${encodeURIComponent(id)}?${params}`, {
    method: "DELETE",
    csrf,
  });
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
  // Fetch from the PUBLIC endpoint — no auth needed.
  const res = await fetch("https://api.corvin-labs.com/v1/stats/instances", { signal });
  if (!res.ok) throw new Error(`stats fetch failed: ${res.status}`);
  return res.json();
}
