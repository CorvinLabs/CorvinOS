/**
 * api/profile — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

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

// ── Voice provider status (ADR-0185 M4) ──────────────────────────────

export interface VoiceProviderStatus {
  ready: boolean;
  package_installed: boolean;
  model_present: boolean | null;
  key_configured: boolean | null;
  detail: string;
}

export interface VoiceStatusResponse {
  stt: Record<string, VoiceProviderStatus>;
  tts: Record<string, VoiceProviderStatus>;
}

export async function getVoiceStatus(signal?: AbortSignal): Promise<VoiceStatusResponse> {
  return api<VoiceStatusResponse>("/voice/status", { signal });
}
