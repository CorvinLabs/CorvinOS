/**
 * LocalDraftComposer: the draft lives in the composer, not in the parent.
 *
 * Proves the three things the chat page relies on: a keystroke re-renders
 * only the composer (not its parent), submit receives the live draft, and a
 * changed resetKey clears it. Also that the memoised ComposerBase skips
 * re-renders when a parent re-renders with unchanged props.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ComposerBase, LocalDraftComposer } from "@/components/ui/composer-base";

vi.mock("@/hooks/use-voice-input", () => ({
  useVoiceInput: () => ({
    recording: false,
    startRecording: vi.fn(async () => {}),
    stopRecording: vi.fn(),
  }),
}));

afterEach(cleanup);

const stableRenderPreButtons = () => null;

describe("LocalDraftComposer", () => {
  it("keeps keystrokes out of the parent render", () => {
    let parentRenders = 0;
    const onSubmit = vi.fn();
    function Parent() {
      parentRenders++;
      return (
        <LocalDraftComposer
          csrf="t"
          onSubmit={onSubmit}
          textareaTestId="ta"
          micTestId="mic"
        />
      );
    }
    render(<Parent />);
    const ta = screen.getByTestId("ta") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "h" } });
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.change(ta, { target: { value: "hi there" } });
    expect(ta.value).toBe("hi there");
    expect(parentRenders).toBe(1);
  });

  it("submits the live draft on Enter and clears it when resetKey changes", () => {
    const onSubmit = vi.fn();
    const { rerender } = render(
      <LocalDraftComposer csrf="t" onSubmit={onSubmit} resetKey="a" textareaTestId="ta" micTestId="mic" />,
    );
    const ta = screen.getByTestId("ta") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "draft text" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("draft text");

    rerender(
      <LocalDraftComposer csrf="t" onSubmit={onSubmit} resetKey="b" textareaTestId="ta" micTestId="mic" />,
    );
    expect(ta.value).toBe("");
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSubmit).toHaveBeenLastCalledWith("");
  });

  it("reports each keystroke through onDraftChange when the parent asks for it", () => {
    const onDraftChange = vi.fn();
    render(
      <LocalDraftComposer csrf="t" onSubmit={vi.fn()} onDraftChange={onDraftChange} textareaTestId="ta" micTestId="mic" />,
    );
    fireEvent.change(screen.getByTestId("ta"), { target: { value: "x" } });
    expect(onDraftChange).toHaveBeenCalledWith("x");
  });

  it("hands the live draft to renderPostButton", () => {
    const seen: string[] = [];
    render(
      <LocalDraftComposer
        csrf="t"
        onSubmit={vi.fn()}
        textareaTestId="ta"
        micTestId="mic"
        renderPostButton={({ draft }) => {
          seen.push(draft);
          return null;
        }}
      />,
    );
    fireEvent.change(screen.getByTestId("ta"), { target: { value: "abc" } });
    expect(seen[seen.length - 1]).toBe("abc");
  });
});

describe("ComposerBase memo", () => {
  it("does not re-render when the parent re-renders with the same props", () => {
    const renderPreButtons = vi.fn(stableRenderPreButtons);
    const props = { value: "x", onChange: vi.fn(), onSubmit: vi.fn(), csrf: "t", renderPreButtons };
    const { rerender } = render(<ComposerBase {...props} />);
    const before = renderPreButtons.mock.calls.length;
    rerender(<ComposerBase {...props} />);
    expect(renderPreButtons.mock.calls.length).toBe(before);
  });
});
