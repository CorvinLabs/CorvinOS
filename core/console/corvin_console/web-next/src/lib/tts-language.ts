/**
 * tts-language — which language the console SPEAKS a reply in, from the
 * operator's profile (`GET /v1/console/profile`).
 *
 * `display_language` has two writers with different meanings (profile.py,
 * 2026-10-21): the installer SEEDS it from the system locale (a default), the
 * operator PINS it in Settings → Profile. A pin is canonical for TTS
 * (maintainer decision 2026-07-20): the reply is spoken in it whatever the
 * text looks like. A seed is NOT: on a fresh `en_US` install the chat page
 * treated the seed as a pin and spoke every German answer with the English
 * voice, and the on-demand session recap was made in English for a German
 * chat (measured on a fresh system, round 2). Only `display_language_source ===
 * "operator"` pins; a backend that does not report the source (older than
 * 2026-10-21) keeps the pin behaviour it had.
 */
import type { ProfileSnapshot } from "@/lib/api/profile";

export interface TtsLanguageChoice {
  /** The operator's pin, or "" — the only value that overrides per-reply detection. */
  pin: string;
  /** The configured language regardless of who set it (pin or seed), or "". */
  configured: string;
}

export function ttsLanguageChoice(snapshot: ProfileSnapshot | null | undefined): TtsLanguageChoice {
  const configured = (snapshot?.identity?.display_language ?? "").trim();
  if (!configured) return { pin: "", configured: "" };
  const source = snapshot?.display_language_source;
  // undefined → old backend (every value was a pin); "" cannot happen with a value.
  const pin = source === "installer" ? "" : configured;
  return { pin, configured };
}

/** The fallback language for a reply whose own language cannot be told:
 *  the configured language (pin or seed), else the browser's UI language. */
export function ttsFallbackLanguage(snapshot: ProfileSnapshot | null | undefined, navigatorLanguage: string): string {
  const { configured } = ttsLanguageChoice(snapshot);
  if (configured) return configured;
  return (navigatorLanguage || "").split("-")[0].toLowerCase() || "en";
}
