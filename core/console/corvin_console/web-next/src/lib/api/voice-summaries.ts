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
