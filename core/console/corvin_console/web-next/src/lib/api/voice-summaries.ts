/**
 * api/voice-summaries — the cross-chat "Voice Summaries" library
 * (GET /v1/console/voice/summaries, routes/voice.py::list_voice_summaries).
 *
 * These are automatically generated, server-persisted whole-session recaps
 * (chat_runtime.py's _spawn_session_summary_auto), distinct from the
 * ephemeral on-demand recap button (playSessionSummary in useVoicePlayback.ts)
 * and from the live per-turn "speak this reply" playback.
 */
import { api } from "./client";

export interface VoiceSummary {
  sid: string;
  title: string;
  /** Epoch seconds. */
  created_at: number | null;
  lang: string | null;
  text: string;
  /** Relative to the API origin; playable directly as an <audio src>. */
  audio_url: string;
}

export interface VoiceSummariesResponse {
  tenant_id: string;
  count: number;
  summaries: VoiceSummary[];
}

export async function listVoiceSummaries(signal?: AbortSignal): Promise<VoiceSummariesResponse> {
  return api<VoiceSummariesResponse>("/voice/summaries", { signal });
}

/** One completed task's persisted recap (GET /voice/task-summaries). */
export interface TaskVoiceSummary {
  sid: string;
  task_id: string | null;
  /** Title of the chat the task ran in. */
  title: string;
  /** Epoch seconds the recap was made. */
  created_at: number | null;
  /** Epoch seconds the TASK finished (falls back to created_at for older recaps). */
  completed_at: number | null;
  lang: string | null;
  text: string;
  /** Relative to the API origin; playable directly as an <audio src>. */
  audio_url: string;
}

export interface TaskVoiceSummariesResponse {
  tenant_id: string;
  count: number;
  /** Server clock, epoch seconds — compare `completed_at` against this, not the browser's clock. */
  now: number;
  summaries: TaskVoiceSummary[];
}

/** Every task recap of the tenant, newest first; `sid` narrows it to one chat. */
export async function listTaskVoiceSummaries(
  sid?: string,
  signal?: AbortSignal,
): Promise<TaskVoiceSummariesResponse> {
  const qs = sid ? `?sid=${encodeURIComponent(sid)}` : "";
  return api<TaskVoiceSummariesResponse>(`/voice/task-summaries${qs}`, { signal });
}

/**
 * Fetch a persisted recap file as a Blob (same-origin, cookie-authenticated) so
 * it can be played through the one gesture-unlocked audio element instead of a
 * fresh `new Audio()` that browsers would autoplay-block.
 */
export async function audioUrlBlob(audioUrl: string, signal?: AbortSignal): Promise<Blob | null> {
  const res = await fetch(audioUrl, { credentials: "include", signal });
  if (res.status === 204 || res.status === 404) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.blob();
}
