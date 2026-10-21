/**
 * ChatComposerField (CONCEPT-0115 P1): the extracted shared composer field.
 * The chat's defaults are unchanged; a workflow context brings its own commands.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ChatComposerField, detectEntityHint, type ComposerFieldHandle } from "@/components/chat/ChatComposerField";
import { checkUploadLimits, DEFAULT_COMPOSER_LIMITS } from "@/components/chat/composer-adapter";

vi.mock("@/hooks/use-voice-input", () => ({
  useVoiceInput: () => ({ recording: false, startRecording: vi.fn(async () => {}), stopRecording: vi.fn() }),
}));

afterEach(cleanup);

function mount(extra: Partial<React.ComponentProps<typeof ChatComposerField>> = {}) {
  const onSubmit = vi.fn();
  const ref = React.createRef<ComposerFieldHandle>();
  render(
    <ChatComposerField
      ref={ref}
      placeholder="p"
      disabled={false}
      onSubmit={onSubmit}
      onHasTextChange={() => {}}
      onEntityHintChange={() => {}}
      {...extra}
    />,
  );
  return { onSubmit, ref, ta: screen.getByTestId(extra.textareaTestId ?? "chat-composer-textarea") };
}

describe("ChatComposerField", () => {
  it("submits on Enter with the typed text, Shift+Enter does not", () => {
    const { onSubmit, ta } = mount();
    fireEvent.change(ta, { target: { value: "hello" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("hello");
  });

  it("chat default shows the session slash table", () => {
    const { ta } = mount();
    fireEvent.change(ta, { target: { value: "/he" } });
    expect(screen.getByTestId("slash-palette").textContent).toContain("/help");
  });

  it("a context with sessionCommands=false shows only its own commands", () => {
    const cmds = [{ cmd: "/design", args: "<text>", desc: "Describe the workflow" }];
    const { ta } = mount({ extraCommands: cmds, sessionCommands: false });
    fireEvent.change(ta, { target: { value: "/" } });
    const pal = screen.getByTestId("slash-palette");
    expect(pal.textContent).toContain("/design");
    expect(pal.textContent).not.toContain("/help");
  });

  it("imperative handle reads and writes the draft", () => {
    const { ref, ta } = mount();
    React.act(() => ref.current!.setValue("from handle"));
    expect((ta as HTMLTextAreaElement).value).toBe("from handle");
    expect(ref.current!.getValue()).toBe("from handle");
  });

  it("detectEntityHint is unchanged", () => {
    expect(detectEntityHint("workflow: x")).toBe("Workflow");
    expect(detectEntityHint("hi")).toBeNull();
  });
});

describe("checkUploadLimits", () => {
  it("refuses an oversized file before upload and accepts a normal one", () => {
    const big = new File(["x"], "big.bin");
    Object.defineProperty(big, "size", { value: DEFAULT_COMPOSER_LIMITS.maxBytes + 1 });
    expect(checkUploadLimits([big], DEFAULT_COMPOSER_LIMITS)).toMatch(/larger than 50 MiB/);
    expect(checkUploadLimits([new File(["x"], "a.txt")], DEFAULT_COMPOSER_LIMITS)).toBeNull();
  });

  it("refuses one file too many, counting what the composer already holds (server: 10 per message)", () => {
    const files = Array.from({ length: 11 }, (_, i) => new File(["x"], `f${i}.txt`));
    expect(checkUploadLimits(files, DEFAULT_COMPOSER_LIMITS)).toMatch(/at most 10 attachments/);
    expect(checkUploadLimits(files.slice(0, 10), DEFAULT_COMPOSER_LIMITS)).toBeNull();
    expect(checkUploadLimits(files.slice(0, 1), DEFAULT_COMPOSER_LIMITS, 10)).toMatch(/10 already added/);
    expect(checkUploadLimits(files.slice(0, 1), DEFAULT_COMPOSER_LIMITS, 9)).toBeNull();
  });
});
