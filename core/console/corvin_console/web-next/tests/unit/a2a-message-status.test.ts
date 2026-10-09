/**
 * The status symbol mapping (src/lib/a2a-message-status.ts): every state a peer-chat
 * message can be in → one symbol, one label, the whole chain. Honesty is the point:
 * a symbol never claims more than was observed.
 */
import { describe, it, expect } from "vitest";
import type { A2AFeedMessage, A2AStageInfo } from "@/lib/api/a2a";
import { QUEUED_STALE_S, formatAge, messageStatusView } from "@/lib/a2a-message-status";

const NOW = 10_000;

function msg(p: Partial<A2AFeedMessage>): A2AFeedMessage {
  return {
    id: "m", ts: NOW - 5, direction: "out", kind: "task", peer_id: "p", peer_label: "Peer",
    task_id: "t", status: "sent", text: "hi", data: {}, attachments: [], duration_ms: null,
    error: null, thread_ref: null, ...p,
  };
}
const stage = (s: A2AStageInfo["stage"], ageS = 3, reason = ""): A2AStageInfo =>
  ({ stage: s, stage_seq: 1, ts: NOW - ageS, reason });
const reply = (status: string, error: string | null = null) => ({ status, error, direction: "in" as const });
const states = (v: ReturnType<typeof messageStatusView>) => v.chain.map((c) => c.state).join(",");

describe("outbound message", () => {
  it("queued shows a clock and the chain at step 0", () => {
    const v = messageStatusView(msg({ status: "queued" }), { now: NOW });
    expect([v.key, v.icon, v.tone]).toEqual(["queued", "clock", "muted"]);
    expect(states(v)).toBe("current,pending,pending,pending,pending,pending");
  });

  it("queued far past the longest send timeout reads as NOT sent, never as pending", () => {
    const v = messageStatusView(msg({ status: "queued", ts: NOW - QUEUED_STALE_S - 1 }), { now: NOW });
    expect([v.icon, v.tone, v.label]).toEqual(["x", "danger", "Not sent"]);
  });

  it("sent with no peer progress is a single tick and says the peer has not reported", () => {
    const v = messageStatusView(msg({ status: "sent" }), { now: NOW });
    expect([v.key, v.icon]).toEqual(["sent", "check"]);
    expect(v.detail).toMatch(/not reported/);
  });

  it.each([
    ["delivered", "delivered", "check-check", "info"],
    ["accepted", "accepted", "check-check", "info"],
    ["processing", "working", "spinner", "working"],
  ] as const)("peer stage %s -> %s", (st, key, icon, tone) => {
    const v = messageStatusView(msg({ status: "sent" }), { now: NOW, stage: stage(st) });
    expect([v.key, v.icon, v.tone]).toEqual([key, icon, tone]);
    expect(v.observedAgeS).toBe(3);
  });

  it("the chain advances monotonically with the stage", () => {
    const pos = (st: A2AStageInfo["stage"]) =>
      states(messageStatusView(msg({}), { now: NOW, stage: stage(st) })).split(",").indexOf("current");
    expect([pos("delivered"), pos("accepted"), pos("processing")]).toEqual([2, 3, 4]);
  });

  it("completed stage = done even before the answer arrived", () => {
    const v = messageStatusView(msg({}), { now: NOW, stage: stage("completed") });
    expect([v.key, v.icon, v.tone]).toEqual(["done", "check-check", "success"]);
    expect(states(v)).toBe("done,done,done,done,done,current");
  });

  it("an answer with no stage at all is done", () => {
    const v = messageStatusView(msg({}), { now: NOW, reply: reply("ok") });
    expect(v.key).toBe("done");
  });

  it.each([["failed"], ["rejected"], ["timeout"]] as const)("terminal peer stage %s is a cross and carries the reason", (st) => {
    const v = messageStatusView(msg({}), { now: NOW, stage: stage(st, 2, "busy") });
    expect([v.icon, v.tone]).toEqual(["x", "danger"]);
    expect(v.detail).toMatch(/busy with other tasks/);
  });

  it("a peer stage outranks a locally inferred state", () => {
    const v = messageStatusView(msg({ status: "unconfirmed" }), { now: NOW, stage: stage("completed") });
    expect(v.key).toBe("done");
  });

  it("unconfirmed with NO observation is a question mark and tells the user not to resend blindly", () => {
    const v = messageStatusView(msg({ status: "sent" }), { now: NOW, reply: reply("unconfirmed") });
    expect([v.icon, v.tone]).toEqual(["help", "warning"]);
    expect(v.detail).toMatch(/before resending/);
  });

  it("unconfirmed WITH an observation shows the last known stage instead of a dead end", () => {
    const v = messageStatusView(msg({ status: "sent" }), { now: NOW, reply: reply("unconfirmed"), stage: stage("processing", 40) });
    expect(v.key).toBe("working");
    expect(v.detail).toMatch(/last known state/);
    expect(v.detail).toMatch(/40 s ago/);
  });

  it.each([["rejected", "Refused"], ["timeout", "Timed out"], ["error", "Failed"]])("an %s answer is a cross labelled %s", (status, label) => {
    const v = messageStatusView(msg({}), { now: NOW, reply: reply(status) });
    expect([v.icon, v.label]).toEqual(["x", label]);
  });

  it("the task record's own failure status is honoured when no reply exists", () => {
    const v = messageStatusView(msg({ status: "error", error: "Unable to reach endpoint" }), { now: NOW });
    expect([v.icon, v.label]).toEqual(["x", "Failed"]);
    expect(v.detail).toMatch(/Unable to reach endpoint/);
  });
});

describe("inbound message and replies", () => {
  it("a received message that is not answered yet", () => {
    const v = messageStatusView(msg({ direction: "in", status: "received" }), { now: NOW });
    expect([v.key, v.icon]).toEqual(["in-received", "check"]);
  });
  it("a received message we answered", () => {
    const v = messageStatusView(msg({ direction: "in", status: "received" }), { now: NOW, reply: { status: "ok", error: null, direction: "out" } });
    expect([v.key, v.icon]).toEqual(["in-answered", "check-check"]);
  });
  it("a received message we refused", () => {
    const v = messageStatusView(msg({ direction: "in", status: "received" }), { now: NOW, reply: { status: "rejected", error: null, direction: "out" } });
    expect([v.key, v.icon]).toEqual(["in-refused", "x"]);
  });
  it("replies carry a symbol too (ok, refused, unconfirmed)", () => {
    expect(messageStatusView(msg({ kind: "response", direction: "in", status: "ok" }), { now: NOW }).key).toBe("reply-ok");
    expect(messageStatusView(msg({ kind: "response", direction: "in", status: "rejected" }), { now: NOW }).icon).toBe("x");
    expect(messageStatusView(msg({ kind: "response", direction: "in", status: "unconfirmed" }), { now: NOW }).icon).toBe("help");
  });
});

describe("robustness", () => {
  it("never throws on odd input and always returns a symbol", () => {
    for (const status of ["", "weird", "OK", "Sent"]) {
      const v = messageStatusView(msg({ status }), { now: NOW });
      expect(v.icon).toBeTruthy();
      expect(v.label).toBeTruthy();
    }
  });
  it("a stage timestamp in the future clamps to 'just now'", () => {
    const v = messageStatusView(msg({}), { now: NOW, stage: { stage: "processing", stage_seq: 1, ts: NOW + 99, reason: "" } });
    expect(v.observedAgeS).toBe(0);
    expect(formatAge(v.observedAgeS)).toBe("just now");
  });
  it("formats ages", () => {
    expect([formatAge(null), formatAge(2), formatAge(30), formatAge(120), formatAge(7300)])
      .toEqual(["", "just now", "30 s ago", "2 min ago", "2 h ago"]);
  });
  it.each([
    ["engine_failed", /usage limit or sign-in problem/],
    ["engine_unavailable", /installed and signed in/],
    ["house_rules_unavailable", /unavailable \(fail-closed\)/],
    ["quota", /daily compute limit.*10 agent tasks per day.*00:00 UTC/],
  ])("the worker refusal %s is explained in the tooltip", (reason, re) => {
    const v = messageStatusView(msg({}), { now: NOW, stage: stage("rejected", 2, reason) });
    expect(v.icon).toBe("x");
    expect(v.detail).toMatch(re);
  });
  it("an unknown reason is not echoed into the UI", () => {
    const v = messageStatusView(msg({}), { now: NOW, stage: stage("failed", 1, "<script>alert(1)</script>") });
    expect(v.detail).not.toMatch(/script/);
  });
});
