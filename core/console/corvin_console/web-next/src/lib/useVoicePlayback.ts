import * as React from "react";
import { ttsBlob } from "@/lib/api";

export type VoiceState = "idle" | "loading" | "playing" | "blocked";

/**
 * Shared TTS playback engine — extracted from chat.tsx's original inline
 * implementation so any page (chat, the first-boot Welcome screen, …) can
 * speak text through the exact same ttsBlob → audioRef → play() mechanism,
 * including the browser-autoplay-block fallback (`voiceState === "blocked"`
 * — audio is ready, caller renders a "tap to hear" affordance that invokes
 * `playBlocked`), instead of re-implementing it at every call site.
 */
export function useVoicePlayback(csrf: string, onError?: (message: string) => void) {
  const [voiceState, setVoiceState] = React.useState<VoiceState>("idle");
  const audioRef = React.useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = React.useRef<string | null>(null);
  // Regression guard: two overlapping playTts() calls used to race — a
  // slower, OLDER request's ttsBlob() could resolve AFTER a newer request
  // already started playing, unconditionally clobbering blobUrlRef/audio.src
  // with its own (stale) blob. The newer blob's object URL was then never
  // revoked (leaked) and playback silently jumped back to the older audio.
  // Each call captures the current generation and only applies its result if
  // it's still the latest one once the async fetch resolves.
  const requestIdRef = React.useRef(0);

  const stopVoice = React.useCallback(() => {
    const a = audioRef.current;
    if (a) {
      try {
        a.pause();
        a.currentTime = 0;
      } catch {
        /* ignore */
      }
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
    setVoiceState("idle");
  }, []);

  // Clean up the audio element on unmount.
  React.useEffect(() => () => stopVoice(), [stopVoice]);

  const playTts = React.useCallback(
    async (text: string, lang: string) => {
      const myRequestId = ++requestIdRef.current;
      // Latest request wins — stop any in-flight playback first.
      stopVoice();
      if (!text.trim()) return;
      setVoiceState("loading");
      let blob: Blob;
      try {
        blob = await ttsBlob(text, lang, csrf);
      } catch (e) {
        if (myRequestId !== requestIdRef.current) return; // superseded meanwhile
        setVoiceState("idle");
        onError?.(e instanceof Error ? `TTS failed: ${e.message}` : "TTS failed");
        return;
      }
      if (myRequestId !== requestIdRef.current) {
        // A newer playTts() call started while this fetch was in flight —
        // it has already set up its own blobUrlRef/audio.src; applying this
        // stale response now would clobber that and leak the never-revoked
        // object URL this call is about to create, so bail out first.
        return;
      }
      if (!blob.size) {
        setVoiceState("idle");
        return;
      }
      const url = URL.createObjectURL(blob);
      blobUrlRef.current = url;
      let audio = audioRef.current;
      if (!audio) {
        audio = new Audio();
        audioRef.current = audio;
      }
      audio.onended = () => {
        if (blobUrlRef.current === url) {
          URL.revokeObjectURL(url);
          blobUrlRef.current = null;
        }
        setVoiceState("idle");
      };
      audio.onerror = () => setVoiceState("idle");
      audio.src = url;
      try {
        await audio.play();
        setVoiceState("playing");
      } catch {
        // Browser blocked autoplay (no user gesture in scope). The audio
        // is ready; the caller shows a "tap to hear" affordance that calls
        // playBlocked() from within a real click handler.
        setVoiceState("blocked");
      }
    },
    [csrf, onError, stopVoice],
  );

  const playBlocked = React.useCallback(async () => {
    const a = audioRef.current;
    if (!a) return;
    try {
      await a.play();
      setVoiceState("playing");
    } catch {
      // Still blocked — leave state as-is so the "tap to hear" affordance stays visible.
    }
  }, []);

  return { voiceState, playTts, playBlocked, stopVoice };
}
