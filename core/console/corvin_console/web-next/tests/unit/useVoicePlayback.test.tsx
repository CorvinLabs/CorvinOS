import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useVoicePlayback } from '@/lib/useVoicePlayback';

vi.mock('@/lib/api', () => ({
  ttsBlob: vi.fn(async () => new Blob(['fake-audio-bytes'], { type: 'audio/mpeg' })),
}));

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
