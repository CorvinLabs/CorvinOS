import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { AudioPlayer } from "../../../src/components/AudioPlayer";

// Regression guards for the two refutation-confirmed AudioPlayer breaks:
//  (A) a benign AbortError/NotAllowedError from play() must NOT replace the
//      player with a permanent "Audio unavailable" card.
//  (B) a stale error state must reset when the `src` prop changes, because
//      chat.tsx keys ArtifactCard by list index → React reuses the instance.

describe("AudioPlayer error handling", () => {
  let playMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    playMock = vi.fn().mockResolvedValue(undefined);
    // happy-dom's HTMLMediaElement leaves play/pause/load unimplemented.
    Object.defineProperty(HTMLMediaElement.prototype, "play", {
      configurable: true,
      value: playMock,
    });
    Object.defineProperty(HTMLMediaElement.prototype, "pause", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(HTMLMediaElement.prototype, "load", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => cleanup());

  it("does not show the error card when play() rejects with AbortError", async () => {
    const abort = Object.assign(new Error("interrupted"), { name: "AbortError" });
    playMock.mockRejectedValueOnce(abort);

    render(<AudioPlayer src="blob:healthy" />);
    // Click play → play() rejects with AbortError (pause interrupted a pending
    // play). The player must stay usable, not flip to "Audio unavailable".
    const buttons = screen.getAllByRole("button");
    fireEvent.click(buttons[0]);
    await new Promise((r) => setTimeout(r, 20));

    expect(screen.queryByText(/unavailable/i)).toBeNull();
  });

  it("does show the error card when play() rejects with a real media error", async () => {
    const decode = Object.assign(new Error("decode failed"), {
      name: "NotSupportedError",
    });
    playMock.mockRejectedValueOnce(decode);

    render(<AudioPlayer src="blob:broken" />);
    fireEvent.click(screen.getAllByRole("button")[0]);

    expect(await screen.findByText(/unavailable/i)).toBeInTheDocument();
  });

  it("clears a stale error state when src changes (index-keyed reuse)", async () => {
    const decode = Object.assign(new Error("decode failed"), {
      name: "NotSupportedError",
    });
    playMock.mockRejectedValueOnce(decode);

    const { rerender } = render(<AudioPlayer src="blob:broken" />);
    fireEvent.click(screen.getAllByRole("button")[0]);
    expect(await screen.findByText(/unavailable/i)).toBeInTheDocument();

    // Same component instance, new healthy source (React reuses via key={index}).
    rerender(<AudioPlayer src="blob:healthy-new" />);
    await waitFor(() =>
      expect(screen.queryByText(/unavailable/i)).toBeNull(),
    );
  });
});
