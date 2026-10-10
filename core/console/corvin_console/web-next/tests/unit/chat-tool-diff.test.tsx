/**
 * ADR-2241: an executed Edit/MultiEdit/Write shows Claude Code's own diff on its
 * tool card — red/green, with a "Show changes" checkbox that starts checked.
 * Driven through the registry's real WebSocket message path (a fake socket stands
 * in for the network), the reload hydration, and the real card component.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { __resetForTests, ensureConnected, getSessionState, sendMessage } from "@/lib/chat-registry";
import { classifyDiffLines } from "@/lib/diff-lines";
import { hydrateChatTurn, ToolUseCard } from "@/pages/chat";

class FakeSocket {
  static last: FakeSocket | null = null;
  static OPEN = 1;
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  constructor(public url: string) { FakeSocket.last = this; }
  send() {}
  close() {}
  emit(evt: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(evt) }); }
}

const SID = "tool-diff-test";
const DIFF = "@@ -1,3 +1,3 @@\n a = 1\n-b = 2\n+b = 20\n c = 3";

function toolPart() {
  const msgs = getSessionState(SID).messages;
  const last = msgs[msgs.length - 1];
  const part = last.parts.find((p) => p.kind === "tool");
  if (!part || part.kind !== "tool") throw new Error("no tool part");
  return part;
}

describe("chat tool diff (ADR-2241)", () => {
  beforeEach(() => {
    __resetForTests();
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
    ensureConnected(SID);
    FakeSocket.last!.onopen?.();
    sendMessage(SID, "change b");
  });
  afterEach(() => { vi.unstubAllGlobals(); __resetForTests(); });

  it("attaches the tool_diff to the card with the same tool-use id", () => {
    const ws = FakeSocket.last!;
    ws.emit({ type: "tool_use", name: "Edit", input: { file_path: "f.py" }, id: "toolu_1" });
    expect(toolPart().diff).toBeUndefined();
    ws.emit({ type: "tool_diff", id: "toolu_other", diff: "@@ -1 +1 @@\n+x", diff_truncated: false });
    expect(toolPart().diff).toBeUndefined();
    ws.emit({ type: "tool_diff", id: "toolu_1", diff: DIFF, diff_truncated: false });
    expect(toolPart().diff).toBe(DIFF);
  });

  it("shows a Bash card's multi-file diff, each hunk naming its file (ADR-2241 amendment)", () => {
    const ws = FakeSocket.last!;
    const bashDiff = "@@ a.py -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3\n@@ b.txt -0,0 +1 @@\n+new";
    ws.emit({ type: "tool_use", name: "Bash", input: { command: "sed -i s/2/3/ a.py" }, id: "toolu_b1" });
    ws.emit({ type: "tool_diff", id: "toolu_b1", diff: bashDiff, diff_truncated: false });
    expect(toolPart().diff).toBe(bashDiff);
    const kinds = classifyDiffLines(bashDiff).map((l) => l.kind);
    expect(kinds).toEqual(["hunk", "ctx", "del", "add", "hunk", "add"]);
    render(<ToolUseCard part={toolPart()} />);
    expect(screen.getByTestId("tool-diff").textContent).toContain("@@ b.txt");
  });

  it("carries a withheld reason instead of text", () => {
    const ws = FakeSocket.last!;
    ws.emit({ type: "tool_use", name: "Write", input: { file_path: ".env" }, id: "toolu_2" });
    ws.emit({ type: "tool_diff", id: "toolu_2", diff_withheld: "credential" });
    expect(toolPart().diff).toBeUndefined();
    expect(toolPart().diffWithheld).toBe("credential");
  });

  it("renders red/green lines, checked by default, and the checkbox hides them", () => {
    render(<ToolUseCard part={{ kind: "tool", name: "Edit", input: {}, id: "t", diff: DIFF }} />);
    const box = screen.getByRole("checkbox", { name: /show changes/i }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    const block = screen.getByTestId("tool-diff");
    expect(block.querySelector('[data-diff="del"]')!.textContent).toBe("-b = 2");
    expect(block.querySelector('[data-diff="add"]')!.textContent).toBe("+b = 20");
    expect(block.querySelector('[data-diff="del"]')!.className).toContain("text-diff-del");
    expect(block.querySelector('[data-diff="add"]')!.className).toContain("text-diff-add");
    expect(block.textContent).toContain("+1");
    fireEvent.click(box);
    expect(block.querySelector('[data-diff="add"]')).toBeNull();
  });

  it("a withheld diff shows the reason, never content", () => {
    render(<ToolUseCard part={{ kind: "tool", name: "Write", input: {}, id: "t", diffWithheld: "credential" }} />);
    expect(screen.getByTestId("tool-diff-withheld").textContent).toMatch(/credential-shaped/);
    expect(screen.queryByTestId("tool-diff")).toBeNull();
  });

  it("survives a reload: the persisted part hydrates back onto the card", () => {
    const m = hydrateChatTurn({ role: "assistant", ts: 1, parts: [
      { kind: "tool", name: "Edit", input: {}, id: "toolu_1", diff: DIFF, diff_truncated: true },
    ] }, 0, SID);
    const p = m.parts[0];
    expect(p.kind === "tool" && p.diff).toBe(DIFF);
    expect(p.kind === "tool" && p.diffTruncated).toBe(true);
  });

  it("classifies by the one prefix every structuredPatch line has", () => {
    expect(classifyDiffLines("@@ -1 +1 @@\n--x\n++y\n  z").map((l) => l.kind)).toEqual(["hunk", "del", "add", "ctx"]);
  });
});
