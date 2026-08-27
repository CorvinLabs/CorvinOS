/**
 * api/memory — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { ApiError, api, isCsrfError, notifyCsrfError } from "./client";

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

/** One segment of the FULL read-aloud (ADR-0194 Phase 3). */
export type TtsSegment = { blob: Blob; total: number; index: number };

/**
 * Fetch ONE segment of the full read-aloud. The server splits the text, so the
 * client never round-trips segment text and the split can evolve without a
 * frontend change.
 *
 * `null` = 204 = "no such segment" (past the end, or past the server's cap) or
 * "no audio" (provider down). Either way the caller stops the playlist — TTS
 * failure is an absent enhancement, never an error banner.
 */
export async function ttsSegment(text: string, lang: string, csrf: string,
                                 sid: string, index: number,
                                 signal?: AbortSignal): Promise<TtsSegment | null> {
  // `signal` lets Stop/supersede ABORT the request instead of merely ignoring
  // the response. Client-side effect only: it stops NEW work from queueing —
  // a synthesis already running in the server threadpool completes and
  // releases its slot on its own schedule (sync handlers are not cancelled
  // on client disconnect).
  const res = await fetch("/v1/console/voice/segment", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify({ text, lang, sid, index }),
    signal,
  });
  if (res.status === 204) return null;
  if (!res.ok) {
    const t = await res.text();
    if (isCsrfError(res.status, t)) notifyCsrfError();
    throw new ApiError(res.status, t);
  }
  // The server owns the real count (it applies its own cap) — trust the header,
  // not a client-side guess at how the text will split.
  const total = Number(res.headers.get("X-Corvin-Voice-Segments") || "");
  const blob = await res.blob();
  return { blob, total: Number.isFinite(total) && total > 0 ? total : index + 1, index };
}

// The reason the LAST /voice/tts returned no audio (from the server's
// X-Corvin-Voice-Reason header — e.g. "no OPENAI_API_KEY", "OpenAI TTS failed:
// 401 …"). ttsBlob returns an empty Blob on 204 to keep the automatic turn's
// silent-degradation contract, so the reason cannot ride on the return value;
// a manual Replay/read-aloud click reads it here to show WHY instead of a
// generic "unavailable". Best-effort, single-slot: only the newest call's
// reason is kept.
let _lastTtsReason: string | null = null;
export function getLastTtsReason(): string | null {
  return _lastTtsReason;
}

export async function ttsBlob(text: string, lang: string, csrf: string,
                              sid?: string, signal?: AbortSignal,
                              systemGenerated?: boolean): Promise<Blob> {
  // `signal`: see ttsSegment — client-side abort on Stop/supersede; an
  // already-running server synthesis finishes on its own schedule.
  // `systemGenerated`: explicit trust flag (e.g. the first-boot welcome
  // greeting) — skips server-side summarization for trusted, pre-composed
  // system text. Must be set EXPLICITLY by a caller that means it; the
  // server no longer infers this from sid being absent (2026-07-29 — that
  // inference skipped summarization for ANY sid-less request, including a
  // real answer that simply didn't pass a session id).
  const res = await fetch("/v1/console/voice/tts", {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
    },
    // ADR-0194 Phase 1: naming the session archives this audio into the session's
    // voice/ dir, so the turn keeps a replayable player. Callers without a chat
    // session (e.g. the first-boot greeting) omit it and get the old behaviour.
    body: JSON.stringify({
      text, lang,
      ...(sid ? { sid } : {}),
      ...(systemGenerated ? { system_generated: true } : {}),
    }),
    signal,
  });
  if (res.status === 204) {
    _lastTtsReason = res.headers.get("X-Corvin-Voice-Reason");
    return new Blob();
  }
  if (!res.ok) {
    const errText = await res.text();
    // Stale csrf → refresh the session so the next turn's TTS succeeds without
    // a manual page reload (the whole reason the automatic voice note broke).
    if (isCsrfError(res.status, errText)) notifyCsrfError();
    throw new ApiError(res.status, errText);
  }
  _lastTtsReason = null;
  return res.blob();
}

/**
 * Spoken recap of the WHOLE session (goal / method / current state) — not
 * one turn. Deliberately not idempotent: the server picks a fresh framing
 * angle and re-runs the summarizer on every call, so pressing this again
 * comes back worded differently, on purpose (user-requested: "immer
 * irgendein anderer Content").
 *
 * `null` = 204 = no audio (nothing to summarize yet, or TTS unavailable) —
 * same silent-degradation contract as every other voice endpoint.
 */
export async function sessionSummaryBlob(sid: string, lang: string,
                                         csrf: string,
                                         signal?: AbortSignal): Promise<Blob | null> {
  // `signal`: see ttsSegment — a recap runs summarizer + TTS server-side, the
  // single longest-held voice slot, so cancelling really must abort it.
  const res = await fetch("/v1/console/voice/session-summary", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify({ sid, lang }),
    signal,
  });
  if (res.status === 204) return null;
  if (!res.ok) {
    const t = await res.text();
    if (isCsrfError(res.status, t)) notifyCsrfError();
    throw new ApiError(res.status, t);
  }
  return res.blob();
}
