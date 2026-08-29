import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useVoicePlayback } from '@/lib/useVoicePlayback';
import { ttsBlob, ttsSegment } from '@/lib/api';

vi.mock('@/lib/api', () => ({
  ttsBlob: vi.fn(async () => new Blob(['fake-audio-bytes'], { type: 'audio/mpeg' })),
  ttsSegment: vi.fn(),
  getLastTtsReason: vi.fn(() => null),
  sessionSummaryBlob: vi.fn(async () => new Blob(['fake-audio-bytes'], { type: 'audio/mpeg' })),
}));

/** A manually-resolvable promise, so a test can control exactly when (and in
 * what order) an in-flight `ttsBlob()` call settles relative to other calls
 * or to cancellation actions (`stopVoice()`, unmount). */
function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/**
 * Regression: the gesture-unlock mechanism (added to fix "only the first
 * chat turn is spoken, every later turn is silent") reused the SAME shared
 * <audio> element real playback uses, and touched it unconditionally on the
 * very first pointerdown/keydown/touchstart anywhere on the page — including
 * when that gesture IS the click on the first-boot Welcome screen's
 * "Tap to hear Corvin" button, whose own click handler is about to resume
 * playback on that exact element. unlock() firing first (capture phase,
 * before the click's bubble-phase onClick) overwrote .src with a silent
 * priming clip and revoked the real greeting's blob URL — the user taps
 * "hear it" and hears nothing, on the one screen this must never happen on.
 */
describe('useVoicePlayback — gesture-unlock must not clobber real/blocked playback', () => {
  let playMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    if (!('createObjectURL' in URL)) {
      // @ts-expect-error - happy-dom may not implement this
      URL.createObjectURL = vi.fn(() => 'blob:fake-url');
    }
    if (!('revokeObjectURL' in URL)) {
      // @ts-expect-error - happy-dom may not implement this
      URL.revokeObjectURL = vi.fn();
    }
    // happy-dom doesn't implement real media playback — every call rejects,
    // simulating the browser's autoplay-block path (the common case a
    // "Tap to hear Corvin" fallback exists for).
    playMock = vi.fn().mockRejectedValue(new DOMException('blocked', 'NotAllowedError'));
    HTMLMediaElement.prototype.play = playMock;
    HTMLMediaElement.prototype.pause = vi.fn();
  });

  it('does not re-touch the audio element via unlock() while a blocked real clip is loaded on it', async () => {
    const { result } = renderHook(() => useVoicePlayback('csrf-token'));

    await act(async () => {
      await result.current.playTts('Hallo, ich bin Corvin.', 'de');
    });

    expect(result.current.voiceState).toBe('blocked');
    expect(playMock).toHaveBeenCalledTimes(1); // only the real playTts attempt so far

    // The user's first-ever gesture on the page — e.g. clicking "Tap to hear
    // Corvin" — used to trigger unlock() first via the window-level capture
    // listener, clobbering the element before the click's own play attempt
    // could even run.
    await act(async () => {
      window.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
      await Promise.resolve();
    });

    // Regression guard: unlock() must bail (real content is still loaded on
    // the element) instead of issuing a second play() call that would
    // overwrite .src with the silent priming clip and revoke the real blob.
    expect(playMock).toHaveBeenCalledTimes(1);
  });

  it('still primes the element on a genuine first gesture when nothing is loaded yet', async () => {
    const { result } = renderHook(() => useVoicePlayback('csrf-token'));

    // No playTts() has ever run — a plain, unrelated first click anywhere
    // on the page must still perform the priming play/pause cycle.
    await act(async () => {
      window.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
      await Promise.resolve();
    });

    expect(playMock).toHaveBeenCalledTimes(1);
  });
});

/**
 * FIXED (2026-07): stopVoice() bumps requestIdRef (and since 2026-07-17 also
 * aborts the in-flight fetch via AbortController), so a playTts() call that
 * is still awaiting ttsBlob() when stopVoice() runs learns it was superseded
 * and never creates a blob URL, sets audio.src, or calls audio.play() after
 * the user asked for silence. These tests were originally written as
 * `it.fails` to pin the then-broken behavior; they are plain `it` now and
 * guard against a regression of exactly that race.
 */
describe('useVoicePlayback — stopVoice() should cancel an in-flight playTts() fetch (voice-off / Stop race)', () => {
  let playMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    playMock = vi.fn().mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = playMock;
    HTMLMediaElement.prototype.pause = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it(
    'does not call audio.play() once ttsBlob() resolves after stopVoice() was already called',
    async () => {
      const deferred = createDeferred<Blob>();
      vi.mocked(ttsBlob).mockImplementationOnce(() => deferred.promise);

      const { result } = renderHook(() => useVoicePlayback('csrf-token'));

      let pending!: Promise<void>;
      act(() => {
        pending = result.current.playTts('Hallo, ich bin Corvin.', 'de');
      });

      // User flips voice off (or hits Stop) WHILE the TTS fetch is still
      // pending — this is exactly what chat.tsx's handleVoiceToggle and the
      // VoicePlaybackChip's onStop do.
      act(() => {
        result.current.stopVoice();
      });

      await act(async () => {
        deferred.resolve(new Blob(['late-audio-bytes'], { type: 'audio/mpeg' }));
        await pending;
      });

      expect(playMock).not.toHaveBeenCalled();
    },
  );
});

/**
 * FIXED (2026-07): chat.tsx renders `<ChatPane key={activeSid} .../>`, so
 * switching sessions unmounts the ChatPane and its useVoicePlayback
 * instance. The unmount cleanup's stopVoice() now bumps requestIdRef and
 * aborts the in-flight fetch, so a playTts() still awaiting ttsBlob() at
 * unmount never creates a blob URL or plays audio for an abandoned session.
 * Originally written as `it.fails` to pin the then-broken behavior; plain
 * `it` now guards against a regression of the session-switch race.
 */
describe('useVoicePlayback — unmounting mid-fetch should cancel a pending playTts() (session-switch race)', () => {
  let playMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    playMock = vi.fn().mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = playMock;
    HTMLMediaElement.prototype.pause = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it(
    'does not call audio.play() or create a blob URL after the hook has been unmounted',
    async () => {
      const deferred = createDeferred<Blob>();
      vi.mocked(ttsBlob).mockImplementationOnce(() => deferred.promise);
      const createObjectURLSpy = vi.spyOn(URL, 'createObjectURL');

      const { result, unmount } = renderHook(() => useVoicePlayback('csrf-token'));

      let pending!: Promise<void>;
      act(() => {
        pending = result.current.playTts('Hallo, ich bin Corvin.', 'de');
      });

      // Session switch: the owning ChatPane (keyed on activeSid) unmounts
      // while the TTS fetch is still in flight.
      unmount();

      await act(async () => {
        deferred.resolve(new Blob(['late-audio-bytes'], { type: 'audio/mpeg' }));
        await pending;
      });

      expect(createObjectURLSpy).not.toHaveBeenCalled();
      expect(playMock).not.toHaveBeenCalled();
    },
  );
});

/**
 * Regression guard for the exact race the code's own comments describe (see
 * `requestIdRef` in useVoicePlayback.ts): an older, slower playTts() request
 * must NOT be allowed to resolve after a newer one and clobber blobUrlRef /
 * audio.src / re-trigger audio.play() with its own stale blob. This is the
 * one scenario the current requestIdRef check is supposed to already handle
 * correctly — unlike the two `.fails` blocks above, this must pass today.
 */
describe('useVoicePlayback — overlapping playTts() calls (requestIdRef guard)', () => {
  let playMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    playMock = vi.fn().mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = playMock;
    HTMLMediaElement.prototype.pause = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('drops an older request that resolves after a newer one, applying only the newest blob', async () => {
    const blobA = new Blob(['A-bytes'], { type: 'audio/mpeg' });
    const blobB = new Blob(['B-bytes'], { type: 'audio/mpeg' });
    const deferredA = createDeferred<Blob>();
    const deferredB = createDeferred<Blob>();
    vi.mocked(ttsBlob).mockImplementationOnce(() => deferredA.promise);
    vi.mocked(ttsBlob).mockImplementationOnce(() => deferredB.promise);
    const createObjectURLSpy = vi.spyOn(URL, 'createObjectURL');
    const revokeObjectURLSpy = vi.spyOn(URL, 'revokeObjectURL');

    const { result } = renderHook(() => useVoicePlayback('csrf-token'));

    let pendingA!: Promise<void>;
    act(() => {
      pendingA = result.current.playTts('Message A', 'de');
    });
    let pendingB!: Promise<void>;
    act(() => {
      pendingB = result.current.playTts('Message B', 'de');
    });

    // Resolve the OLDER request (A) first, out of order.
    await act(async () => {
      deferredA.resolve(blobA);
      await pendingA;
    });

    // A must have been dropped: no blob URL created, no play() attempt yet.
    expect(createObjectURLSpy).not.toHaveBeenCalled();
    expect(playMock).not.toHaveBeenCalled();

    // Now resolve the NEWER request (B).
    await act(async () => {
      deferredB.resolve(blobB);
      await pendingB;
    });

    // Only B's blob is ever created/played; A never leaks a blob URL because
    // it never got far enough to create one in the first place.
    expect(createObjectURLSpy).toHaveBeenCalledTimes(1);
    expect(createObjectURLSpy).toHaveBeenCalledWith(blobB);
    expect(playMock).toHaveBeenCalledTimes(1);
    expect(revokeObjectURLSpy).not.toHaveBeenCalled(); // nothing to revoke yet — playback hasn't ended
    expect(result.current.voiceState).toBe('playing');
  });
});

/**
 * The existing suite only ever mocks `HTMLMediaElement.prototype.play` to
 * REJECT (simulating the browser autoplay-block path). The success branch —
 * `voiceState` becoming "playing", `onended`'s blob-URL cleanup, and the
 * asymmetric `onerror` cleanup this review closed — was entirely untested.
 */
describe('useVoicePlayback — playTts() success path (audio.play() resolves)', () => {
  let playMock: ReturnType<typeof vi.fn>;
  let createdAudioEls: HTMLAudioElement[];
  let OriginalAudio: typeof Audio;

  beforeEach(() => {
    playMock = vi.fn().mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = playMock;
    HTMLMediaElement.prototype.pause = vi.fn();

    // Capture every `new Audio()` instance the hook creates so a test can
    // manually invoke its onended/onerror handlers (the hook itself does not
    // expose the underlying element).
    createdAudioEls = [];
    OriginalAudio = window.Audio;
    window.Audio = new Proxy(OriginalAudio, {
      construct(target, args) {
        const instance = Reflect.construct(target, args as ConstructorParameters<typeof Audio>);
        createdAudioEls.push(instance as HTMLAudioElement);
        return instance;
      },
    }) as unknown as typeof Audio;
  });

  afterEach(() => {
    window.Audio = OriginalAudio;
    vi.restoreAllMocks();
  });

  it('transitions to "playing" and marks the element unlocked when audio.play() resolves', async () => {
    const { result } = renderHook(() => useVoicePlayback('csrf-token'));

    await act(async () => {
      await result.current.playTts('Hallo, ich bin Corvin.', 'de');
    });

    expect(result.current.voiceState).toBe('playing');
    expect(playMock).toHaveBeenCalledTimes(1);

    // A successful play() is itself proof the element is unlocked: a later
    // pointerdown must NOT trigger a second, redundant priming play() call.
    await act(async () => {
      window.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
      await Promise.resolve();
    });
    expect(playMock).toHaveBeenCalledTimes(1);
  });

  it('onended revokes the blob URL and returns voiceState to "idle" when playback completes naturally', async () => {
    const revokeObjectURLSpy = vi.spyOn(URL, 'revokeObjectURL');
    const { result } = renderHook(() => useVoicePlayback('csrf-token'));

    await act(async () => {
      await result.current.playTts('Hallo, ich bin Corvin.', 'de');
    });

    expect(result.current.voiceState).toBe('playing');
    expect(createdAudioEls).toHaveLength(1);
    const audioEl = createdAudioEls[0];
    expect(typeof audioEl.onended).toBe('function');

    act(() => {
      audioEl.onended?.(new Event('ended'));
    });

    expect(revokeObjectURLSpy).toHaveBeenCalledTimes(1);
    expect(result.current.voiceState).toBe('idle');
  });

  it('onerror revokes the blob URL (parity with onended) instead of leaking it', async () => {
    const revokeObjectURLSpy = vi.spyOn(URL, 'revokeObjectURL');
    const { result } = renderHook(() => useVoicePlayback('csrf-token'));

    await act(async () => {
      await result.current.playTts('Hallo, ich bin Corvin.', 'de');
    });

    expect(result.current.voiceState).toBe('playing');
    expect(createdAudioEls).toHaveLength(1);
    const audioEl = createdAudioEls[0];
    expect(typeof audioEl.onerror).toBe('function');

    act(() => {
      audioEl.onerror?.(new Event('error'));
    });

    // Regression guard: previously, onerror reset voiceState to "idle" but
    // never revoked the blob URL or cleared blobUrlRef — leaking the object
    // URL and leaving the ref desynced from the (now broken) idle state.
    expect(revokeObjectURLSpy).toHaveBeenCalledTimes(1);
    expect(result.current.voiceState).toBe('idle');
  });
});

/**
 * 2026-08-04 adversarial review, live report: "the voice summary in the
 * Console chat sometimes gets cut off / doesn't come out complete."
 *
 * playFull()'s own loop bound (`while (index < total)`) already stops
 * fetching once the playlist is genuinely exhausted (the next segment fetch
 * is skipped in favour of a local `Promise.resolve(null)` once
 * `index + 1 >= total`) — so a 204/null response for a segment where
 * `index` is still < the already-known `total` can only be a genuine
 * synthesis failure (subprocess timeout, provider chain exhaustion, a
 * concurrency slot-wait timeout), never the natural end of the playlist.
 * Before this fix, that case was treated identically to a normal end and
 * playback stopped with ZERO indication to the user — exactly the reported
 * symptom, once playback had already been running successfully for one or
 * more segments.
 */
describe('useVoicePlayback — playFull() mid-playlist segment failure (2026-08-04 adversarial review)', () => {
  let playMock: ReturnType<typeof vi.fn>;
  let createdAudioEls: HTMLAudioElement[];
  let OriginalAudio: typeof Audio;

  beforeEach(() => {
    playMock = vi.fn().mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = playMock;
    HTMLMediaElement.prototype.pause = vi.fn();
    if (!('createObjectURL' in URL)) {
      // @ts-expect-error - happy-dom may not implement this
      URL.createObjectURL = vi.fn(() => 'blob:fake-url');
    }
    if (!('revokeObjectURL' in URL)) {
      // @ts-expect-error - happy-dom may not implement this
      URL.revokeObjectURL = vi.fn();
    }

    createdAudioEls = [];
    OriginalAudio = window.Audio;
    window.Audio = new Proxy(OriginalAudio, {
      construct(target, args) {
        const instance = Reflect.construct(target, args as ConstructorParameters<typeof Audio>);
        createdAudioEls.push(instance as HTMLAudioElement);
        return instance;
      },
    }) as unknown as typeof Audio;

    vi.mocked(ttsSegment).mockReset();
  });

  afterEach(() => {
    window.Audio = OriginalAudio;
    vi.restoreAllMocks();
  });

  it('calls onError instead of silently stopping when a mid-playlist segment comes back empty', async () => {
    // Segment 0 succeeds and reports total=3 (three segments expected).
    // Segment 1 simulates a genuine synthesis failure: null, exactly what a
    // 204 from /voice/segment maps to.
    vi.mocked(ttsSegment).mockImplementation(async (_text, _lang, _csrf, _sid, index) => {
      if (index === 0) {
        return { blob: new Blob(['seg0'], { type: 'audio/mpeg' }), total: 3, index: 0 };
      }
      if (index === 1) {
        return null;
      }
      throw new Error(`unexpected fetch for index ${index} — playlist should have stopped`);
    });

    const onError = vi.fn();
    const { result } = renderHook(() => useVoicePlayback('csrf-token', onError));

    let done!: Promise<void>;
    act(() => {
      done = result.current.playFull('Eine lange Antwort mit mehreren Segmenten.', 'de', 'sid-1');
    });

    // Let segment 0's fetch resolve and audio.play() settle.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(createdAudioEls).toHaveLength(1);
    const audioEl = createdAudioEls[0];

    // Segment 0 "finishes playing" — the loop advances to await segment 1's
    // already-in-flight (mocked) fetch, which resolves null.
    await act(async () => {
      audioEl.onended?.(new Event('ended'));
      await done;
    });

    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/stopped early/i));
    // Regression guard: the pre-fix code called onError only for index === 0
    // (the "no synthesis at all" case) — asserting the exact non-generic
    // message here also guards against silently regressing back to the old
    // catch-all _TTS_UNAVAILABLE_MSG for a mid-playlist failure.
    expect(onError).not.toHaveBeenCalledWith(expect.stringMatching(/check Settings/i));
  });

  it('still uses the "unavailable" message (not "stopped early") when the FIRST segment fails', async () => {
    vi.mocked(ttsSegment).mockResolvedValue(null);

    const onError = vi.fn();
    const { result } = renderHook(() => useVoicePlayback('csrf-token', onError));

    await act(async () => {
      await result.current.playFull('Kurze Antwort.', 'de', 'sid-1');
    });

    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/check Settings/i));
    expect(onError).not.toHaveBeenCalledWith(expect.stringMatching(/stopped early/i));
  });
});
