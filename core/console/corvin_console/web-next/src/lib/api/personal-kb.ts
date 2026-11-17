/**
 * Personal knowledge base ("Only for me") — the owner's encrypted personal space.
 *
 * Backend: /v1/console/personal/* plus the personal scope of the Knowledge Graph and Tasks routes.
 * Every call carries the console session; changes carry the CSRF token. The unlock token is an
 * HttpOnly cookie set by the server, so this module never sees it. A locked space answers 423,
 * a wrong passphrase 403 (never 401, which would sign the user out of the console).
 */
import { api, ApiError, BASE } from "./client";

export type EntityType = "preference" | "context" | "idea" | "task" | "note";

export interface PersonalStatus {
  available: boolean;
  secure_device_key: boolean;
  unlocked: boolean;
  auto_capture?: boolean;
  frozen?: boolean;
  review_pending?: number;
  entries?: number;
  problems?: number;
  policy_pending?: boolean;
  bindings?: { channel: string; display: string }[];
  mode?: "convenient" | "strict";
  /** the tenant's default space: open without a login, no passphrase */
  default?: boolean;
  has_passphrase?: boolean;
  /** why the space could not be opened (a short sentence) */
  reason?: string;
}

export interface DecoderObjection { category: string; text: string; resolved: boolean; stage: string; dismissed_because?: string; rank?: number }
export interface DecoderState {
  stage: "seed" | "shape" | "choices" | "plan" | "done";
  loss: number;
  iterations: number;
  gates: Record<string, { ok: boolean; failures: string[] }>;
  objections: DecoderObjection[];
  children: Record<string, string[]>;
  last_stage?: string;
}

export interface PersonalEntity {
  uid: string;
  display_id?: string;
  type: EntityType;
  title: string;
  body: string;
  status: string;
  origin: "user" | "auto" | "confirmed" | "derived";
  review: "none" | "pending" | "accepted";
  key?: string | null;
  labels: string[];
  links: { rel: string; to: string }[];
  version: number;
  created: number;
  updated: number;
  expires_at?: number | null;
  task?: { dod: string | null; blocked_reason: string | null; cancel_reason: string | null } | null;
  decoder?: DecoderState | null;
  versions?: number[];
}

export interface StageResult {
  stage: string; gate_ok: boolean; failures: string[]; loss: number;
  objections: DecoderObjection[]; created: string[]; duplicates: string[];
}

export interface PolicyView {
  current: Record<string, number | boolean | string>;
  version: number;
  versions: number;
  frozen: boolean;
  pending: { change_id: string; target: Record<string, unknown>; cause?: string } | null;
  allowlist: Record<string, { kind: string; default: unknown; min: number | null; max: number | null; choices: string[] }>;
}

const P = "/personal";

export const isLocked = (e: unknown) => e instanceof ApiError && e.status === 423;

export const getStatus = (signal?: AbortSignal) => api<PersonalStatus>(`${P}/status`, { signal });
export const createSpace = (body: { handle: string; passphrase: string; consent: boolean; unlock_mode: "convenient" | "strict" }, csrf: string) =>
  api<{ created: boolean; recovery_code: string }>(`${P}/owner`, { method: "POST", csrf, body, timeoutMs: 60_000 });
export const unlockSpace = (handle: string, passphrase: string, csrf: string) =>
  api<{ unlocked: boolean }>(`${P}/unlock`, { method: "POST", csrf, body: { handle, passphrase }, timeoutMs: 60_000 });
export const unlockWithRecovery = (handle: string, recovery_code: string, new_passphrase: string, csrf: string) =>
  api<{ unlocked: boolean }>(`${P}/unlock/recovery`, { method: "POST", csrf, body: { handle, recovery_code, new_passphrase }, timeoutMs: 60_000 });
export const removeUnreadable = (csrf: string) => api<{ removed: number }>(`${P}/repair`, { method: "POST", csrf });
export const lockSpace = (csrf: string) => api<{ unlocked: boolean }>(`${P}/lock`, { method: "POST", csrf });

export const listEntities = (signal?: AbortSignal, params: { type?: EntityType; q?: string; review?: string } = {}) => {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]).toString();
  return api<{ entities: PersonalEntity[]; problems: { uid: string; problem: string }[] }>(`${P}/entities${qs ? `?${qs}` : ""}`, { signal });
};
export const getEntity = (uid: string, signal?: AbortSignal) => api<PersonalEntity & { versions?: number[] }>(`${P}/entities/${encodeURIComponent(uid)}`, { signal });
export const createEntity = (body: { type: EntityType; title: string; body?: string; key?: string | null }, csrf: string) =>
  api<PersonalEntity>(`${P}/entities`, { method: "POST", csrf, body });
export const updateEntity = (uid: string, body: { title?: string; body?: string; status?: string; expected_version?: number }, csrf: string) =>
  api<PersonalEntity>(`${P}/entities/${encodeURIComponent(uid)}`, { method: "PATCH", csrf, body });
export const forgetEntity = (uid: string, csrf: string) =>
  api<{ forgotten: boolean; drafts_removed?: number }>(`${P}/entities/${encodeURIComponent(uid)}`, { method: "DELETE", csrf });
export const restoreEntity = (uid: string, version: number, csrf: string) =>
  api<PersonalEntity>(`${P}/entities/${encodeURIComponent(uid)}/restore`, { method: "POST", csrf, body: { version } });
export const reviewEntity = (uid: string, action: "accept" | "edit" | "forget", csrf: string, new_title?: string) =>
  api<{ entity: PersonalEntity | null }>(`${P}/entities/${encodeURIComponent(uid)}/review`, { method: "POST", csrf, body: { action, new_title } });
export const promoteIdea = (uid: string, summary: string, body: string, target: "idea" | "plan", csrf: string) =>
  api<{ promoted: boolean; project_id: string }>(`${P}/entities/${encodeURIComponent(uid)}/promote`, { method: "POST", csrf, body: { summary, body, target }, timeoutMs: 120_000 });

export const transitionTask = (uid: string, to: string, csrf: string, extra: { reason?: string; dod?: string } = {}) =>
  api<PersonalEntity>(`${P}/tasks/${encodeURIComponent(uid)}/transition`, { method: "POST", csrf, body: { to, ...extra } });

export const getDecoder = (uid: string, signal?: AbortSignal) =>
  api<{ idea: PersonalEntity; decoder: DecoderState; next_stage: string | null; critic_check?: { runs: number; missed: number; last: "found" | "missed" | null } }>(`${P}/ideas/${encodeURIComponent(uid)}/decoder`, { signal });
export const unfoldIdea = (uid: string, csrf: string, stage?: string) =>
  api<StageResult>(`${P}/ideas/${encodeURIComponent(uid)}/unfold`, { method: "POST", csrf, body: { stage: stage ?? null }, timeoutMs: 600_000 });
/** ``expect`` = the text of the remark the owner is looking at: the server refuses if the list changed meanwhile. */
export const resolveObjection = (uid: string, index: number, action: "fix" | "dismiss", csrf: string, reason = "", expect?: string) =>
  api<StageResult | { dismissed: boolean }>(`${P}/ideas/${encodeURIComponent(uid)}/decoder/objection`, { method: "POST", csrf, body: { index, action, reason, expect: expect?.slice(0, 80) }, timeoutMs: 600_000 });
/** "Close as realized": the server checks again that the idea's current plan is finished. */
export const closeIdea = (uid: string, csrf: string) =>
  api<{ closed: boolean }>(`${P}/ideas/${encodeURIComponent(uid)}/decoder/close`, { method: "POST", csrf });
export const revisitTask = (ideaUid: string, taskUid: string, category: string, csrf: string) =>
  api<StageResult | { revisit: null }>(`${P}/ideas/${encodeURIComponent(ideaUid)}/decoder/revisit`, { method: "POST", csrf, body: { task_uid: taskUid, category }, timeoutMs: 600_000 });

/** What the consistency sweep wants the owner to look at (ADR-2329): judgement calls only, never auto-applied. */
export interface ReviewItem {
  code: "DUPLICATE_IDEAS" | "STUCK_REALIZATION" | "DECODER_STATE_LOST" | "DUPLICATE_CHECK_CAPPED" | string;
  uid: string; display_id?: string | null; title: string;
  other_uid?: string | null; other_display_id?: string | null; other_title?: string;
}
export const getConsistency = (signal?: AbortSignal) =>
  api<{ items: ReviewItem[]; healed: number; auto_left: number; healed_on_open?: number }>(`${P}/consistency`, { signal });
export const dismissReview = (ideaUid: string, code: string, csrf: string) =>
  api<{ dismissed: string }>(`${P}/ideas/${encodeURIComponent(ideaUid)}/decoder/dismiss`, { method: "POST", csrf, body: { code } });
export const acknowledgePair = (a: string, b: string, csrf: string) =>
  api<{ acknowledged: boolean }>(`${P}/consistency/acknowledge`, { method: "POST", csrf, body: { a, b } });

/** The critic's objections in its own order of importance, for the step that ran last: its first open objection is the
 *  main point, its other open ones are quiet, and open objections of earlier steps are listed apart (also quiet). */
export function splitObjections<T extends { resolved: boolean; rank?: number; stage?: string }>(objs: T[], lastStage?: string): {
  main: [T, number] | null; rest: [T, number][]; earlier: [T, number][]; done: [T, number][];
} {
  const indexed = objs.map((o, i) => [o, i] as [T, number]);
  const current = (o: T) => !lastStage || !o.stage || o.stage === lastStage;
  const open = indexed.filter(([o]) => !o.resolved && current(o)).sort((a, b) => (a[0].rank ?? a[1] + 1) - (b[0].rank ?? b[1] + 1));
  return { main: open[0] ?? null, rest: open.slice(1), earlier: indexed.filter(([o]) => !o.resolved && !current(o)),
           done: indexed.filter(([o]) => o.resolved) };
}

export const getFeed = (signal?: AbortSignal) => api<{ entries: PersonalEntity[] }>(`${P}/feed`, { signal });
export const getPolicy = (signal?: AbortSignal) => api<PolicyView>(`${P}/policy`, { signal });
export const editPolicy = (changes: Record<string, unknown>, csrf: string) =>
  api<{ outcome: string; version: number }>(`${P}/policy`, { method: "PUT", csrf, body: { changes } });
export const rollbackPolicy = (to_version: number, csrf: string) =>
  api<{ outcome: string }>(`${P}/policy/rollback`, { method: "POST", csrf, body: { to_version } });
export const confirmPolicy = (change_id: string, accept: boolean, csrf: string) =>
  api<{ outcome: string }>(`${P}/policy/confirm`, { method: "POST", csrf, body: { change_id, accept } });
export const unfreezePolicy = (csrf: string) => api<{ frozen: boolean }>(`${P}/policy/unfreeze`, { method: "POST", csrf });
export const setAutoCapture = (auto_capture: boolean, csrf: string) =>
  api<{ auto_capture: boolean }>(`${P}/settings`, { method: "POST", csrf, body: { auto_capture } });

export const issuePairCode = (csrf: string) => api<{ code: string; expires_in_s: number }>(`${P}/pair`, { method: "POST", csrf });
export const pendingPairs = (signal?: AbortSignal) =>
  api<{ pending: { pair_id: string; channel: string; display: string; masked: string }[] }>(`${P}/pair/pending`, { signal });
export const confirmPair = (pair_id: string, csrf: string) =>
  api<{ linked: { channel: string; display: string; masked: string }; migrated: number }>(`${P}/pair/confirm`, { method: "POST", csrf, body: { pair_id } });
export const unbindIdentity = (index: number, csrf: string) =>
  api<{ unbound: boolean }>(`${P}/identity/unbind`, { method: "POST", csrf, body: { index } });

export const changePassphrase = (old: string, next: string, csrf: string) =>
  api<{ changed: boolean }>(`${P}/passphrase`, { method: "POST", csrf, body: { old, new: next }, timeoutMs: 60_000 });
export const rekeySpace = (passphrase: string, csrf: string) =>
  api<{ rekeyed: boolean; recovery_code: string }>(`${P}/rekey`, { method: "POST", csrf, body: { passphrase }, timeoutMs: 600_000 });
export const eraseSpace = (passphrase: string, also_chat_history: boolean, csrf: string) =>
  api<{ erased: boolean; entries: number }>(`${P}/erase`, { method: "POST", csrf, body: { passphrase, also_chat_history }, timeoutMs: 600_000 });

export const getPersonalGraph = (signal?: AbortSignal) =>
  api<{ entities: { id: string; label: string; type: string; title: string; status: string; tags: string[] }[];
        relations: { from_id: string; to_id: string; relation: string }[]; unlocked: boolean }>(
    `/plugins/corvin-knowledge/graph?scope=personal`, { signal });

/** The export is built in memory on the server and streamed; the browser saves it as a file. */
export async function downloadExport(): Promise<Blob> {
  const res = await fetch(`${BASE}${P}/export`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.blob();
}

/** Plain-language labels for the decoder's stages (shown in the stepper). */
export const STAGE_LABELS: Record<string, string> = {
  seed: "Seed", shape: "Shape", choices: "Choices", plan: "Plan", done: "Done",
};

/** "Open issues" from a loss: the share of failed checks, shown as a whole-number percentage. */
export function openIssuesLabel(loss: number | null | undefined): string {
  if (loss === null || loss === undefined || Number.isNaN(loss)) return "not measured yet";
  if (loss <= 0) return "no open issues";
  return `${Math.round(loss * 100)}% of checks open`;
}
