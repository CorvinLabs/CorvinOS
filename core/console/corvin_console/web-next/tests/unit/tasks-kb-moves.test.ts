import { describe, expect, it } from "vitest";
import { KB_MOVES, canMove, isKbItem } from "@/pages/tasks/encodings";

const kb = (kind: string, status: string) => ({ external_ref: "kb:01J00000000000000000000000", kind, status }) as never;
const local = (status: string) => ({ external_ref: null, kind: "task", status }) as never;

describe("knowledge-base board moves", () => {
  it("recognises KB items by their ref", () => {
    expect(isKbItem({ external_ref: "kb:X" })).toBe(true);
    expect(isKbItem({ external_ref: "git:CorvinOS#ADR-0001" })).toBe(false);
    expect(isKbItem({ external_ref: null })).toBe(false);
  });
  it("follows the KB state machine: open never jumps to complete, done is terminal", () => {
    expect(canMove(kb("task", "open"), "in_progress")).toBe(true);
    expect(canMove(kb("task", "open"), "complete")).toBe(false);
    expect(canMove(kb("task", "in_progress"), "complete")).toBe(true);
    expect(canMove(kb("task", "complete"), "open")).toBe(false);
    expect(KB_MOVES.complete).toEqual([]);
  });
  it("never moves a KB container — its status is derived", () => {
    expect(canMove(kb("epic", "open"), "in_progress")).toBe(false);
    expect(canMove(kb("initiative", "in_progress"), "complete")).toBe(false);
  });
  it("leaves local items free", () => {
    expect(canMove(local("open"), "complete")).toBe(true);
    expect(canMove(local("open"), "open")).toBe(false);
  });
});
