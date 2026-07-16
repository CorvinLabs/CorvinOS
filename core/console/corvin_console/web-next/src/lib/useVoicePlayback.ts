import * as React from "react";
import { ttsBlob, ttsSegment, sessionSummaryBlob, type TtsSegment } from "@/lib/api";

export type VoiceState = "idle" | "loading" | "playing" | "blocked";

// A 1-frame SILENT WAV. Played (muted) inside a real user gesture to satisfy
// every browser's autoplay policy ONCE, so all later programmatic playTts()
// calls — which run AFTER an async summarize/tts fetch, far from any gesture —
// are allowed. Without this, browsers (Firefox strictest) auto-play the FIRST
// turn (it still falls inside the send gesture's activation window) but BLOCK
// every turn after it, so the second task onward is silently never spoken.
const _SILENT_WAV =
  "data:audio/wav;base64,UklGRjIAAABXQVZFZm10IBIAAAABAAEAQB8AAEAfAAABAAgAAABmYWN0BAAAAAAAAABkYXRhAAAAAA==";

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
  const unlockedRef = React.useRef(false);

  const ensureAudioEl = React.useCallback(() => {
    if (!audioRef.current) audioRef.current = new Audio();
    return audioRef.current;
  }, []);

  // Prime the (reused) audio element inside a user gesture so the browser marks
  // it user-activated — then every later programmatic play() is allowed. Runs
  // once; idempotent no-op after the first success. Best-effort: a failed prime
  // (no active gesture yet) leaves unlockedRef false so the NEXT gesture retries.
  //
  // Regression guard: this MUST NOT touch the element while it holds real TTS
  // content (blobUrlRef set — loaded, playing, or blocked-awaiting-a-tap). The
  // capture-phase pointerdown below fires BEFORE any bubble-phase onClick, so
  // the exact "tap to hear Corvin" gesture on a blocked first-boot greeting
  // used to hit this function first, overwrite audio.src with the silent
  // priming clip, and then its own .then() paused/reset the element and let
  // the stale onended handler revoke the greeting's blob URL — the user taps
  // "hear it" and hears nothing, on the one screen this must never happen on.
  // If real content is already loaded, that content's own play()/play-blocked
  // affordance already IS the activation-consuming attempt; skip priming and
  // let the next gesture (after this element frees up) retry.
  const unlock = React.useCallback(() => {
    if (unlockedRef.current || blobUrlRef.current) return;
    const a = ensureAudioEl();
    try {
      a.muted = true;
      a.src = _SILENT_WAV;
      const p = a.play();
      if (p && typeof p.then === "function") {
        p.then(() => {
          // Narrow residual race: real content can be loaded onto this same
          // element by playTts() while this priming play() was in flight
          // (blobUrlRef was null when unlock() started, so its guard above
          // let it through). If so, don't pause/reset what's now playing —
          // just record that priming succeeded; the real playback continues.
          if (!blobUrlRef.current) {
            try { a.pause(); a.currentTime = 0; } catch { /* ignore */ }
          }
          a.muted = false;
          unlockedRef.current = true;
        }).catch(() => { a.muted = false; });
      } else {
        a.muted = false;
        unlockedRef.current = true;
      }
    } catch {
      a.muted = false;
    }
  }, [ensureAudioEl]);

  // Default-ON, zero-config: the FIRST user interaction anywhere on the page
  // primes audio, so voice works from turn 1 on every browser/OS with no caller
  // wiring. Listeners stay until the prime succeeds (unlock() self-no-ops after),
  // so a gesture that arrives before React is ready still catches the next one.
  React.useEffect(() => {
    if (unlockedRef.current) return;
    const h = () => unlock();
    const opts = { capture: true } as const;
    window.addEventListener("pointerdown", h, opts);
    window.addEventListener("keydown", h, opts);
    window.addEventListener("touchstart", h, opts);
    return () => {
      window.removeEventListener("pointerdown", h, opts);
      window.removeEventListener("keydown", h, opts);
      window.removeEventListener("touchstart", h, opts);
    };
  }, [unlock]);

  const stopVoice = React.useCallback(() => {
    // Bump the generation FIRST. stopVoice() used to be the only playback-state
    // mutator that left requestIdRef alone, which made an explicit user Stop
    // indistinguishable from "nothing happened" to every in-flight call: each
    // one tests `myRequestId !== requestIdRef.current` to detect being
    // superseded, so a Stop pressed while a TTS fetch was still in flight was
    // simply ignored and Corvin started speaking the answer the user had just
    // silenced. Bumping here makes Stop a real supersede, which is also what
    // lets playFull's segment loop notice it and bail out.
    requestIdRef.current += 1;
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
    async (text: string, lang: string, sid?: string) => {
      // Order matters: stopVoice() ITSELF bumps the generation (that is what
      // makes an explicit Stop a real supersede), so the id must be captured
      // AFTER it. Capturing first and then calling stopVoice() made every call
      // supersede ITSELF — the guard after the ttsBlob await would compare a
      // stale id and bail, i.e. voice would never play at all.
      stopVoice();  // latest request wins — stop any in-flight playback first
      const myRequestId = ++requestIdRef.current;
      if (!text.trim()) return;
      setVoiceState("loading");
      let blob: Blob;
      try {
        blob = await ttsBlob(text, lang, csrf, sid);
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
      // Reuse the ONE (gesture-unlocked) element — a fresh `new Audio()` per turn
      // would not carry the user-activation and would be autoplay-blocked.
      const audio = ensureAudioEl();
      audio.muted = false;
      audio.onended = () => {
        if (blobUrlRef.current === url) {
          URL.revokeObjectURL(url);
          blobUrlRef.current = null;
        }
        setVoiceState("idle");
      };
      audio.onerror = () => {
        // Mirror onended's cleanup: without this, a playback error left
        // blobUrlRef pointing at a URL that was never revoked (leaked) and
        // out of sync with voiceState ("idle" while a stale blob URL/src was
        // still referenced) until the next stopVoice()/playTts() happened to
        // clean it up incidentally.
        if (blobUrlRef.current === url) {
          URL.revokeObjectURL(url);
          blobUrlRef.current = null;
        }
        setVoiceState("idle");
      };
      audio.src = url;
      try {
        await audio.play();
        setVoiceState("playing");
        // A successful play() on this element — whether autoplayed within
        // the send gesture's activation window, or (see playBlocked below)
        // resumed from a direct tap — is itself definitive proof the
        // element is unlocked. Mark it so the priming listeners in the
        // effect above stop doing redundant work.
        unlockedRef.current = true;
      } catch {
        // pause() REJECTS a pending play() with AbortError, which is
        // indistinguishable here from a real autoplay block. Without this
        // guard, a Stop pressed during the ~100ms play() window left the chip
        // stuck at "tap to hear" — and since Replay and "read the full answer
        // aloud" both render only on voiceState === "idle", both stayed hidden
        // until the user pressed Stop a second time. Worse, tapping the
        // affordance called playBlocked(), which replayed the very audio the
        // user had just silenced. A superseded call owns nothing: bail out.
        if (myRequestId !== requestIdRef.current) return;
        // Browser blocked autoplay (no user gesture in scope). The audio
        // is ready; the caller shows a "tap to hear" affordance that calls
        // playBlocked() from within a real click handler.
        setVoiceState("blocked");
      }
    },
    [csrf, onError, stopVoice, ensureAudioEl],
  );

  /**
   * Speak the WHOLE answer as a sequential playlist (ADR-0194 Phase 3).
   *
   * `playTts` speaks a ≤400-char summary — by construction a long answer is never
   * read out. This walks the server's segmentation instead, one request per
   * segment, and PREFETCHES segment i+1 while i is playing: audio starts in
   * seconds rather than after the whole answer is synthesised, with no background
   * jobs or polling on either side.
   *
   * Shares this hook's hard-won invariants deliberately: the ONE gesture-unlocked
   * element (a fresh `new Audio()` per segment would be autoplay-blocked from the
   * second segment on), the requestId supersede (a newer play wins mid-playlist),
   * and per-segment object-URL revocation (a 24-segment read-aloud leaking a blob
   * each would be a real leak, not a rounding error).
   */
  const playFull = React.useCallback(
    async (text: string, lang: string, sid: string) => {
      // See playTts: stopVoice() bumps the generation, so capture AFTER it.
      stopVoice();
      const myRequestId = ++requestIdRef.current;
      if (!text.trim()) return;
      setVoiceState("loading");
      const audio = ensureAudioEl();
      let index = 0;
      let total = Number.POSITIVE_INFINITY;
      let pending: Promise<TtsSegment | null> = ttsSegment(text, lang, csrf, sid, 0);
      try {
        while (index < total) {
          let seg: TtsSegment | null = null;
          try {
            seg = await pending;
          } catch (e) {
            if (myRequestId === requestIdRef.current) {
              onError?.(e instanceof Error ? `TTS failed: ${e.message}` : "TTS failed");
            }
            return;
          }
          // A newer playTts/playFull started while this fetch was in flight — it
          // owns the element now; applying this would clobber it and leak the URL.
          if (myRequestId !== requestIdRef.current) return;
          if (!seg || !seg.blob.size) return;  // 204 → end of playlist
          total = seg.total;
          // Kick off the NEXT fetch before playing this one — the whole point.
          pending = index + 1 < total
            ? ttsSegment(text, lang, csrf, sid, index + 1)
            : Promise.resolve(null);
          // Every `return` below abandons this prefetch un-awaited (Stop, a
          // supersede, end-of-playlist). Attaching a no-op catch marks the
          // rejection handled — otherwise a dropped connection or a 500 on the
          // orphaned segment surfaced as "Uncaught (in promise) ApiError". This
          // does NOT swallow it for the awaiter: `.catch()` returns a NEW
          // promise, so `await pending` still throws into the loop's own catch.
          pending.catch(() => { /* handled at the await, if we get there */ });

          const url = URL.createObjectURL(seg.blob);
          blobUrlRef.current = url;
          audio.muted = false;
          audio.src = url;
          try {
            await audio.play();
            setVoiceState("playing");
            unlockedRef.current = true;
          } catch {
            // playFull always starts from a real click, so this is rare; leave the
            // segment loaded so the caller's "tap to hear" affordance can resume it.
            setVoiceState("blocked");
            return;
          }
          // Wait for THIS segment to finish before starting the next. onerror
          // resolves too: one undecodable segment must not strand the playlist.
          // `pause` resolves as well because stopVoice()/a superseding play()
          // PAUSES this element, and pause fires NEITHER `ended` NOR `error` —
          // without it this promise never settled, so pressing Stop mid-playlist
          // suspended the loop for the page's lifetime, permanently retaining the
          // audio element and the already-prefetched next segment, and never
          // revoking the current segment's object URL. The requestId check below
          // then turns that wake-up into a clean bail-out.
          await new Promise<void>((resolve) => {
            audio.onended = () => resolve();
            audio.onerror = () => resolve();
            audio.onpause = () => resolve();
          });
          audio.onpause = null;
          if (blobUrlRef.current === url) {
            URL.revokeObjectURL(url);
            blobUrlRef.current = null;
          }
          if (myRequestId !== requestIdRef.current) return;
          index += 1;
        }
      } finally {
        // Only the still-current request may reset shared state — a superseded
        // one would yank the element out from under the request that replaced it.
        if (myRequestId === requestIdRef.current && blobUrlRef.current === null) {
          setVoiceState("idle");
        }
      }
    },
    [csrf, onError, stopVoice, ensureAudioEl],
  );

  /**
   * Speak a fresh recap of the WHOLE session (goal / method / current state)
   * — not one turn. Reuses playTts's exact shape (single blob, supersede
   * guard, one gesture-unlocked element) since this is single-shot audio
   * too, just from a different endpoint. The one real difference: this is
   * NOT idempotent by design — the server re-runs the summarizer and picks
   * a new framing angle on every call, so pressing this again after it
   * already played gives back different wording, on purpose.
   */
  const playSessionSummary = React.useCallback(
    async (sid: string, lang: string) => {
      // See playTts: stopVoice() bumps the generation, so capture AFTER it.
      stopVoice();
      const myRequestId = ++requestIdRef.current;
      setVoiceState("loading");
      let blob: Blob | null;
      try {
        blob = await sessionSummaryBlob(sid, lang, csrf);
      } catch (e) {
        if (myRequestId !== requestIdRef.current) return;
        setVoiceState("idle");
        onError?.(e instanceof Error ? `Session summary failed: ${e.message}` : "Session summary failed");
        return;
      }
      if (myRequestId !== requestIdRef.current) return; // superseded meanwhile
      if (!blob || !blob.size) {
        setVoiceState("idle");
        return;
      }
      const url = URL.createObjectURL(blob);
      blobUrlRef.current = url;
      const audio = ensureAudioEl();
      audio.muted = false;
      audio.onended = () => {
        if (blobUrlRef.current === url) {
          URL.revokeObjectURL(url);
          blobUrlRef.current = null;
        }
        setVoiceState("idle");
      };
      audio.onerror = () => {
        if (blobUrlRef.current === url) {
          URL.revokeObjectURL(url);
          blobUrlRef.current = null;
        }
        setVoiceState("idle");
      };
      audio.src = url;
      try {
        await audio.play();
        setVoiceState("playing");
        unlockedRef.current = true;
      } catch {
        if (myRequestId !== requestIdRef.current) return;
        setVoiceState("blocked");
      }
    },
    [csrf, onError, stopVoice, ensureAudioEl],
  );

  const playBlocked = React.useCallback(async () => {
    const a = audioRef.current;
    if (!a) return;
    try {
      await a.play();
      setVoiceState("playing");
      unlockedRef.current = true;
    } catch {
      // Still blocked — leave state as-is so the "tap to hear" affordance stays visible.
    }
  }, []);

  return { voiceState, playTts, playFull, playSessionSummary, playBlocked, stopVoice, unlock };
}
