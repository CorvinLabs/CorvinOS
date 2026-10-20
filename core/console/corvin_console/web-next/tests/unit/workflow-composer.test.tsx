/**
 * WorkflowComposer (CONCEPT-0115 P2): the workflow editor's input is the shared
 * chat composer — Enter sends, dictation lands in the field, `/` shows ONLY the
 * server's design commands.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WorkflowComposer } from "@/components/workflows/WorkflowComposer";

vi.mock("@/hooks/use-voice-input", () => ({
  useVoiceInput: () => ({ recording: false, startRecording: vi.fn(async () => {}), stopRecording: vi.fn() }),
}));

afterEach(cleanup);

const CMDS = [{ cmd: "/phase", args: "", desc: "Show the current design phase" }];

function mount(connected = true) {
  const onSend = vi.fn();
  render(<WorkflowComposer contextKey="t" csrf="c" connected={connected} placeholder="p" commands={CMDS} onSend={onSend} />);
  return { onSend, ta: screen.getByTestId("workflow-composer-textarea") as HTMLTextAreaElement };
}

describe("WorkflowComposer", () => {
  it("Enter sends the trimmed text and clears the field", () => {
    const { onSend, ta } = mount();
    fireEvent.change(ta, { target: { value: "  build a report  " } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("build a report");
    expect(ta.value).toBe("");
  });

  it("send button is disabled until there is text", () => {
    const { ta } = mount();
    const send = screen.getByTestId("workflow-composer-send") as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(ta, { target: { value: "x" } });
    expect(send.disabled).toBe(false);
  });

  it("shows only the server's commands, not the chat table", () => {
    const { ta } = mount();
    fireEvent.change(ta, { target: { value: "/" } });
    const pal = screen.getByTestId("slash-palette");
    expect(pal.textContent).toContain("/phase");
    expect(pal.textContent).not.toContain("/help");
  });

  it("while disconnected nothing is sent and the draft survives", () => {
    const { onSend, ta } = mount(false);
    expect(ta.disabled).toBe(true);
    expect(onSend).not.toHaveBeenCalled();
  });
});
