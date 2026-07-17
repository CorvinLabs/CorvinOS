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

// Shown when an EXPLICIT click (Replay, read-full-answer, session recap) got a
// 204 back: on a zero-config box without an LLM/TTS backend the click would
// otherwise do nothing, silently, no matter how often it's pressed. The
// AUTOMATIC turn voice keeps its silent-degradation contract (204 = absent
// enhancement, never an error) — this message is for click paths only.
const _TTS_UNAVAILABLE_MSG =
  "Voice synthesis unavailable — check Settings → Voice (an LLM/TTS backend is needed).";

// play() rejections carry the browser's verdict in `name`: ONLY
// "NotAllowedError" means autoplay-blocked, i.e. a user tap will fix it.
// Anything else (e.g. "NotSupportedError" on undecodable audio) must NOT
// surface the tap-to-play affordance — tapping would re-run the same failure.
const _isAutoplayBlock = (e: unknown): boolean =>
  (e as { name?: string } | null)?.name === "NotAllowedError";

// Fetch rejections caused by our own AbortController (Stop/supersede) — an
// intentional cancellation, never worth an onError toast.
const _isAbort = (e: unknown): boolean =>
  (e as { name?: string } | null)?.name === "AbortError";

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
  // AbortController for the CURRENT generation's voice fetches. Superseding
  // used to only IGNORE the stale response while the server kept synthesising
  // into a held TTS slot for up to ~145 s — aborting here actually releases
  // it. Owned by the same lifecycle as requestIdRef: created after each
  // stopVoice(), aborted inside it.
  const abortRef = React.useRef<AbortController | null>(null);

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
    // Abort the in-flight fetch too — the requestId bump already makes every
    // awaiter bail on resolve, so the resulting AbortError can never reach
    // onError (the supersede guard returns first).
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
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
    async (text: string, lang: string, sid?: string,
           opts?: { notifyOnEmpty?: boolean }) => {
      // Order matters: stopVoice() ITSELF bumps the generation (that is what
      // makes an explicit Stop a real supersede), so the id must be captured
      // AFTER it. Capturing first and then calling stopVoice() made every call
      // supersede ITSELF — the guard after the ttsBlob await would compare a
      // stale id and bail, i.e. voice would never play at all.
      stopVoice();  // latest request wins — stop any in-flight playback first
      const myRequestId = ++requestIdRef.current;
      if (!text.trim()) return;
      setVoiceState("loading");
      const ac = new AbortController();
      abortRef.current = ac;
      let blob: Blob;
      try {
        blob = await ttsBlob(text, lang, csrf, sid, ac.signal);
      } catch (e) {
        if (myRequestId !== requestIdRef.current) return; // superseded meanwhile
        if (_isAbort(e)) return; // our own Stop — intentional, not an error
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
        // 204-silence is the DESIGN for the automatic turn voice, but an
        // explicit click (Replay) opts into feedback — see _TTS_UNAVAILABLE_MSG.
        if (opts?.notifyOnEmpty) onError?.(_TTS_UNAVAILABLE_MSG);
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
      } catch (e) {
        // pause() REJECTS a pending play() with AbortError, which is
        // indistinguishable here from a real autoplay block. Without this
        // guard, a Stop pressed during the ~100ms play() window left the chip
        // stuck at "tap to hear" — and since Replay and "read the full answer
        // aloud" both render only on voiceState === "idle", both stayed hidden
        // until the user pressed Stop a second time. Worse, tapping the
        // affordance called playBlocked(), which replayed the very audio the
        // user had just silenced. A superseded call owns nothing: bail out.
        if (myRequestId !== requestIdRef.current) return;
        if (_isAbort(e)) {
          // SAME-generation AbortError: something paused the element without
          // going through stopVoice() (OS media key, audio-focus loss) inside
          // the ms-wide play() window. Neither a block nor a real failure —
          // reset quietly, no banner.
          if (blobUrlRef.current === url) {
            URL.revokeObjectURL(url);
            blobUrlRef.current = null;
          }
          setVoiceState("idle");
          return;
        }
        if (!_isAutoplayBlock(e)) {
          // Not an autoplay block (e.g. NotSupportedError on undecodable
          // audio) — a "tap to play" affordance would tap into the same
          // failure forever. Reset instead, and say why.
          if (blobUrlRef.current === url) {
            URL.revokeObjectURL(url);
            blobUrlRef.current = null;
          }
          setVoiceState("idle");
          onError?.(e instanceof Error ? `Audio playback failed: ${e.message}` : "Audio playback failed");
          return;
        }
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
      const ac = new AbortController();
      abortRef.current = ac;
      const audio = ensureAudioEl();
      let index = 0;
      let total = Number.POSITIVE_INFINITY;
      let pending: Promise<TtsSegment | null> = ttsSegment(text, lang, csrf, sid, 0, ac.signal);
      try {
        while (index < total) {
          let seg: TtsSegment | null = null;
          try {
            seg = await pending;
          } catch (e) {
            if (myRequestId === requestIdRef.current && !_isAbort(e)) {
              onError?.(e instanceof Error ? `TTS failed: ${e.message}` : "TTS failed");
            }
            return;
          }
          // A newer playTts/playFull started while this fetch was in flight — it
          // owns the element now; applying this would clobber it and leak the URL.
          if (myRequestId !== requestIdRef.current) return;
          if (!seg || !seg.blob.size) {
            // 204 mid-playlist is the normal end-of-playlist signal — but on
            // the FIRST segment it means no synthesis at all (zero-config box
            // without a TTS/LLM backend). playFull always starts from an
            // explicit click, and a click that does nothing, silently,
            // arbitrarily often reads as a dead button — say why, once.
            if (index === 0) onError?.(_TTS_UNAVAILABLE_MSG);
            return;
          }
          total = seg.total;
          // Kick off the NEXT fetch before playing this one — the whole point.
          pending = index + 1 < total
            ? ttsSegment(text, lang, csrf, sid, index + 1, ac.signal)
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
          } catch (e) {
            // See playTts: pause() REJECTS a pending play() with AbortError,
            // indistinguishable here from a real autoplay block. Without this
            // guard, a Stop (or a newer playTts/playFull) that superseded this
            // request mid-play() left the chip stuck at "tap to hear" even
            // though playback was deliberately stopped, not blocked — a
            // superseded call owns nothing and must not touch shared state.
            if (myRequestId !== requestIdRef.current) return;
            if (_isAbort(e)) {
              // SAME-generation AbortError (see playTts): an OS-level pause in
              // the play() window, not a block/failure. End quietly.
              if (blobUrlRef.current === url) {
                URL.revokeObjectURL(url);
                blobUrlRef.current = null;
              }
              setVoiceState("idle");
              return;
            }
            if (!_isAutoplayBlock(e)) {
              // Undecodable/unsupported segment, not an autoplay block: the
              // tap-to-play affordance would re-run the same failure. Reset.
              if (blobUrlRef.current === url) {
                URL.revokeObjectURL(url);
                blobUrlRef.current = null;
              }
              setVoiceState("idle");
              onError?.(e instanceof Error ? `Audio playback failed: ${e.message}` : "Audio playback failed");
              return;
            }
            // Autoplay-blocked mid-playlist (rare — playFull starts from a real
            // click). Returning here used to END the playlist's story: neither
            // onended nor onpause were installed yet, so after the user's "tap
            // to play" segment 0 finished with no state transition at all —
            // voiceState stuck on "playing" forever, the remaining segments
            // silently dropped, the segment's object URL never revoked. Wait
            // for the tap instead (playBlocked() resumes THIS element, firing
            // `playing`) and then continue the playlist loop normally.
            setVoiceState("blocked");
            const resumed = await new Promise<boolean>((resolve) => {
              // stopVoice() while blocked pauses an ALREADY-paused element —
              // no `pause` event fires — so a Stop/supersede can only be seen
              // by polling the generation counter. playBlocked()'s failure
              // path calls stopVoice() too, so a failed resume also wakes
              // this wait through the same poll instead of hanging it.
              const iv = setInterval(() => {
                if (myRequestId !== requestIdRef.current) {
                  cleanup();
                  resolve(false);
                }
              }, 250);
              const cleanup = () => {
                clearInterval(iv);
                audio.onplaying = null;
              };
              audio.onplaying = () => {
                cleanup();
                resolve(true);
              };
            });
            if (!resumed || myRequestId !== requestIdRef.current) return;
            setVoiceState("playing");
            unlockedRef.current = true;
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
      const ac = new AbortController();
      abortRef.current = ac;
      let blob: Blob | null;
      try {
        blob = await sessionSummaryBlob(sid, lang, csrf, ac.signal);
      } catch (e) {
        if (myRequestId !== requestIdRef.current) return;
        if (_isAbort(e)) return; // our own Stop — intentional, not an error
        setVoiceState("idle");
        onError?.(e instanceof Error ? `Session summary failed: ${e.message}` : "Session summary failed");
        return;
      }
      if (myRequestId !== requestIdRef.current) return; // superseded meanwhile
      if (!blob || !blob.size) {
        setVoiceState("idle");
        // The recap only runs from an explicit click — a 204 (zero-config box
        // without an LLM/TTS backend) must not read as a dead button.
        onError?.(_TTS_UNAVAILABLE_MSG);
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
      } catch (e) {
        if (myRequestId !== requestIdRef.current) return;
        if (_isAbort(e)) {
          // SAME-generation AbortError (see playTts): an OS-level pause in
          // the play() window, not a block/failure. Reset quietly.
          if (blobUrlRef.current === url) {
            URL.revokeObjectURL(url);
            blobUrlRef.current = null;
          }
          setVoiceState("idle");
          return;
        }
        if (!_isAutoplayBlock(e)) {
          // See playTts: only a real autoplay block earns the tap-to-play
          // affordance — anything else would make the tap a no-op forever.
          if (blobUrlRef.current === url) {
            URL.revokeObjectURL(url);
            blobUrlRef.current = null;
          }
          setVoiceState("idle");
          onError?.(e instanceof Error ? `Audio playback failed: ${e.message}` : "Audio playback failed");
          return;
        }
        setVoiceState("blocked");
      }
    },
    [csrf, onError, stopVoice, ensureAudioEl],
  );

  const playBlocked = React.useCallback(async () => {
    const a = audioRef.current;
    if (!a) return;
    // Capture the generation at tap time: like every other play()-catch site,
    // this one must be able to tell "this tap was superseded" apart from
    // "this tap genuinely failed". Without it, a Stop/voice-off/newer play*()
    // landing inside the tap's ~100 ms play() window rejected the pending
    // play() with AbortError and fell into the failure path below — which
    // showed a bogus "Audio playback failed" banner, and (worse) its
    // stopVoice() bumped the NEW generation and aborted ITS in-flight fetch,
    // silencing the very turn that superseded this tap.
    const gen = requestIdRef.current;
    try {
      await a.play();
      // A successful play() is proof of unlock regardless of generation…
      unlockedRef.current = true;
      // …but a superseded tap owns no shared state anymore.
      if (requestIdRef.current !== gen) return;
      setVoiceState("playing");
    } catch (e) {
      // Superseded, or pause()'s AbortError on the pending play() (same
      // intentional cancellation, seen from inside the play() call): the tap
      // owns nothing — no banner, and above all no stopVoice() that would
      // sabotage the generation that replaced it.
      if (requestIdRef.current !== gen || _isAbort(e)) return;
      // Still blocked — leave state as-is so the "tap to hear" affordance
      // stays visible and the user can simply tap again.
      if (_isAutoplayBlock(e)) return;
      // A non-block failure (e.g. undecodable audio): tapping again would run
      // the identical failure forever — a dead affordance. stopVoice() resets
      // state and revokes the blob URL AND bumps the generation, which also
      // wakes playFull's blocked-resume wait so a suspended playlist loop can
      // bail out cleanly instead of hanging for the page's lifetime.
      stopVoice();
      onError?.(e instanceof Error ? `Audio playback failed: ${e.message}` : "Audio playback failed");
    }
  }, [onError, stopVoice]);

  return { voiceState, playTts, playFull, playSessionSummary, playBlocked, stopVoice, unlock };
}
