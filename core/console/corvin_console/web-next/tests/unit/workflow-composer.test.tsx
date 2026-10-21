/**
 * WorkflowComposer (CONCEPT-0115 P2/P3): the workflow editor's input is the shared
 * chat composer — Enter sends, dictation lands in the field, `/` shows ONLY the
 * server's design commands, attachments are staged as chips and travel by id.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkflowComposer, type WorkflowComposerHandle } from "@/components/workflows/WorkflowComposer";
import { DEFAULT_COMPOSER_LIMITS, type ComposerContextAdapter } from "@/components/chat/composer-adapter";

vi.mock("@/hooks/use-voice-input", () => ({
  useVoiceInput: () => ({ recording: false, startRecording: vi.fn(async () => {}), stopRecording: vi.fn() }),
}));

afterEach(cleanup);

const CMDS = [{ cmd: "/phase", args: "", desc: "Show the current design phase" }];

function makeAdapter(over: Partial<ComposerContextAdapter> = {}): ComposerContextAdapter {
  return {
    upload: vi.fn(async (files: File[]) => files.map((f, i) => ({ id: `id${i}`, name: f.name, size: f.size, mime: "text/plain" }))),
    remove: vi.fn(async () => {}),
    extraCommands: CMDS,
    sessionCommands: false,
    send: vi.fn(),
    contextKey: "t",
    limits: DEFAULT_COMPOSER_LIMITS,
    ...over,
  };
}

function mount(connected = true, adapter = makeAdapter()) {
  const ref = React.createRef<WorkflowComposerHandle>();
  render(<WorkflowComposer ref={ref} csrf="c" connected={connected} placeholder="p" adapter={adapter} />);
  return { adapter, ref, ta: screen.getByTestId("workflow-composer-textarea") as HTMLTextAreaElement };
}

describe("WorkflowComposer", () => {
  it("Enter sends the trimmed text and clears the field", () => {
    const { adapter, ta } = mount();
    fireEvent.change(ta, { target: { value: "  build a report  " } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(adapter.send).toHaveBeenCalledWith("build a report", []);
    expect(ta.value).toBe("");
  });

  it("a send the adapter refuses synchronously keeps the text and the chips", async () => {
    const adapter = makeAdapter({ send: vi.fn(() => false) });
    const { ref, ta } = mount(true, adapter);
    await act(async () => { ref.current!.addFiles([new File(["a"], "a.txt")]); });
    await waitFor(() => expect(screen.getAllByTestId("remove-attachment")).toHaveLength(1));
    fireEvent.change(ta, { target: { value: "keep me" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(adapter.send).toHaveBeenCalledTimes(1);
    expect(ta.value).toBe("keep me");
    expect(screen.getAllByTestId("remove-attachment")).toHaveLength(1);
  });

  it("restoreAttachments puts uploaded chips back after a server refusal", async () => {
    const { ref } = mount();
    expect(screen.queryAllByTestId("remove-attachment")).toHaveLength(0);
    await act(async () => { ref.current!.restoreAttachments([{ id: "x1", name: "x.txt", size: 1, mime: "text/plain" }]); });
    expect(screen.getAllByTestId("remove-attachment")).toHaveLength(1);
  });

  it("refuses the 11th file before any upload (server allows 10 per message)", async () => {
    const adapter = makeAdapter();
    const { ref } = mount(true, adapter);
    const ten = Array.from({ length: 10 }, (_, i) => new File(["a"], `f${i}.txt`));
    await act(async () => { ref.current!.addFiles(ten); });
    await waitFor(() => expect(screen.getAllByTestId("remove-attachment")).toHaveLength(10));
    await act(async () => { ref.current!.addFiles([new File(["a"], "eleven.txt")]); });
    expect(adapter.upload).toHaveBeenCalledTimes(1);
    expect(screen.getAllByTestId("remove-attachment")).toHaveLength(10);
    expect(screen.getByTestId("workflow-composer").textContent).toMatch(/at most 10 attachments/);
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

  it("while disconnected nothing is sent and the field is disabled", () => {
    const { adapter, ta } = mount(false);
    expect(ta.disabled).toBe(true);
    expect(adapter.send).not.toHaveBeenCalled();
  });

  it("files from the picker become chips; Send carries their ids and clears them", async () => {
    const { adapter, ta } = mount();
    const input = screen.getByTestId("workflow-file-input") as HTMLInputElement;
    const file = new File(["a,b"], "data.csv", { type: "text/csv" });
    await act(async () => { fireEvent.change(input, { target: { files: [file] } }); });
    await waitFor(() => expect(screen.getByTestId("attachment-chip")).toBeTruthy());
    expect(adapter.upload).toHaveBeenCalledWith([file]);
    // an attachment alone is enough to send
    fireEvent.click(screen.getByTestId("workflow-composer-send"));
    expect(adapter.send).toHaveBeenCalledWith("", [expect.objectContaining({ id: "id0", name: "data.csv" })]);
    expect(screen.queryByTestId("attachment-chip")).toBeNull();
    void ta;
  });

  it("removing a chip drops it server-side too", async () => {
    const { adapter } = mount();
    const input = screen.getByTestId("workflow-file-input") as HTMLInputElement;
    await act(async () => { fireEvent.change(input, { target: { files: [new File(["x"], "a.txt")] } }); });
    await waitFor(() => screen.getByTestId("attachment-chip"));
    fireEvent.click(screen.getByTestId("remove-attachment"));
    expect(screen.queryByTestId("attachment-chip")).toBeNull();
    expect(adapter.remove).toHaveBeenCalledWith(expect.objectContaining({ id: "id0" }));
  });

  it("an oversized file is refused client-side without calling upload", async () => {
    const { adapter } = mount();
    const big = new File(["x"], "big.bin");
    Object.defineProperty(big, "size", { value: DEFAULT_COMPOSER_LIMITS.maxBytes + 1 });
    await act(async () => { fireEvent.change(screen.getByTestId("workflow-file-input"), { target: { files: [big] } }); });
    await waitFor(() => expect(screen.getByTestId("workflow-upload-error").textContent).toMatch(/larger than 50 MiB/));
    expect(adapter.upload).not.toHaveBeenCalled();
  });

  it("the handle stages dropped files like the picker does", async () => {
    const { adapter, ref } = mount();
    await act(async () => { ref.current!.addFiles([new File(["x"], "dropped.txt")]); });
    await waitFor(() => expect(screen.getByTestId("attachment-chip").textContent).toContain("dropped.txt"));
    expect(adapter.upload).toHaveBeenCalledTimes(1);
  });
});
