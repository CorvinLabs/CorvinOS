/**
 * CONCEPT-0107 — /btw v2 state machine (pure, lib/btw-steer.ts) and the
 * registry wiring it runs through (lib/chat-registry.ts).
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import {
  btwFromTurn, insertSteerNote, makeSteerMessage, retrySteerState, setSteerState, settleHydrated,
  settleOnDone, stateFromServer, stripBtwPrefix, needsOperatorAction,
} from "@/lib/btw-steer";
import type { ChatMessage } from "@/lib/chat-registry";
import { sendBtwNote } from "@/lib/api/chat";

const user = (id: string): ChatMessage => ({ id, role: "user", parts: [{ kind: "text", text: id }], ts: 1 });
const asst = (id: string, streaming = false): ChatMessage =>
  ({ id, role: "assistant", parts: [], ts: 2, ...(streaming ? { streaming } : {}) });

describe("btw-steer", () => {
  it("prefix is optional and stripped", () => {
    expect(stripBtwPrefix("/btw kürzer bitte")).toBe("kürzer bitte");
    expect(stripBtwPrefix("  /BTW   x ")).toBe("x");
    expect(stripBtwPrefix("kürzer bitte")).toBe("kürzer bitte");
    expect(stripBtwPrefix("/btwx")).toBe("/btwx");
    expect(stripBtwPrefix("/btw")).toBe("");
  });

  it("note goes directly before the running assistant bubble (the persisted order)", () => {
    const msgs = [user("u1"), asst("a1", true)];
    const note = makeSteerMessage("shorter", "text", "c1", 3);
    const out = insertSteerNote(msgs, "a1", note);
    expect(out.map((m) => m.id)).toEqual(["u1", "btw-c1", "a1"]);
    expect(insertSteerNote(msgs, null, note).map((m) => m.id)).toEqual(["u1", "a1", "btw-c1"]);
  });

  it("server status maps onto honest states", () => {
    expect(stateFromServer("injected")).toBe("injected");
    expect(stateFromServer("queued")).toBe("queued");
    expect(stateFromServer("refused")).toBe("refused");
    expect(stateFromServer("no_active_stream")).toBe("undelivered");
  });

  it("every arrow: sending→queued→injected→answered, and never backwards", () => {
    let msgs = insertSteerNote([user("u"), asst("a", true)], "a", makeSteerMessage("x", "voice", "c", 1));
    const st = () => msgs.find((m) => m.btw)!.btw!.state;
    expect(st()).toBe("sending");
    msgs = setSteerState(msgs, "c", "queued");          expect(st()).toBe("queued");
    msgs = setSteerState(msgs, "c", "injected");        expect(st()).toBe("injected");
    msgs = setSteerState(msgs, "c", "queued");          expect(st()).toBe("injected"); // late HTTP answer
    msgs = settleOnDone(msgs, { answered: true });      expect(st()).toBe("answered");
    msgs = setSteerState(msgs, "c", "undelivered");     expect(st()).toBe("answered"); // terminal stays
  });

  it("queued then undelivered when the turn ended first; refused keeps its reason", () => {
    let msgs = [makeSteerMessage("x", "text", "c", 1)];
    msgs = setSteerState(msgs, "c", "queued");
    msgs = setSteerState(msgs, "c", "undelivered");
    expect(msgs[0].btw!.state).toBe("undelivered");
    msgs = settleOnDone(msgs, { answered: true });
    expect(msgs[0].btw!.state).toBe("undelivered");   // done never claims it was answered
    const r = setSteerState([makeSteerMessage("y", "text", "d", 1)], "d", "refused", "rate_limited");
    expect(r[0].btw).toMatchObject({ state: "refused", reason: "rate_limited" });
  });

  it("turn end: a waiting note is undelivered; a delivered one is answered only on a clean end", () => {
    const base = [makeSteerMessage("q", "text", "q1", 1), makeSteerMessage("i", "text", "i1", 1)];
    let msgs = setSteerState(setSteerState(base, "q1", "queued"), "i1", "injected");
    const clean = settleOnDone(msgs, { answered: true });
    expect(clean.map((m) => m.btw!.state)).toEqual(["undelivered", "answered"]);
    const failed = settleOnDone(msgs, { answered: false });          // error-done or /stop
    expect(failed.map((m) => m.btw!.state)).toEqual(["undelivered", "injected"]);
    msgs = setSteerState(base, "q1", "failed");
    msgs = retrySteerState(msgs, "q1");                             // same note, same id
    expect(msgs[0].btw).toMatchObject({ state: "sending", clientId: "q1" });
  });

  it("no-loss: the note's text survives every non-delivered terminal state, with actions", () => {
    for (const s of ["refused", "undelivered", "failed"] as const) {
      const m = setSteerState([makeSteerMessage("keep me", "text", "c", 1)], "c", s);
      expect(m[0].parts).toEqual([{ kind: "text", text: "keep me" }]);
      expect(needsOperatorAction(m[0].btw!.state)).toBe(true);
    }
    expect(needsOperatorAction("answered")).toBe(false);
  });

  it("hydration renders the same bubble after a reload", () => {
    expect(btwFromTurn({ btw: true, btw_status: "injected", via: "voice", client_id: "c9" }, "h0"))
      .toEqual({ state: "injected", via: "voice", clientId: "c9" });
    expect(btwFromTurn({ btw: true, gate_refused: "btw" }, "h1")).toMatchObject({ state: "refused" });
    expect(btwFromTurn({ btw: true }, "h2")).toMatchObject({ state: "injected", via: "text", clientId: "h2" });
    expect(btwFromTurn({}, "h3")).toBeUndefined();
    const noteMsg = (state: "injected" | "undelivered"): ChatMessage =>
      ({ ...makeSteerMessage("n", "text", state, 1), btw: { state, via: "text", clientId: state } });
    const settled = settleHydrated([user("u"), noteMsg("injected"), asst("a"), user("u2"), noteMsg("undelivered")]);
    expect(settled[1].btw!.state).toBe("answered");
    expect(settled[4].btw!.state).toBe("undelivered");
    // a delivered note whose turn has no answer yet is NOT marked answered
    expect(settleHydrated([user("u"), noteMsg("injected")])[1].btw!.state).toBe("injected");
    // …nor one whose turn was stopped
    const stopped: ChatMessage = { id: "a2", role: "assistant", ts: 3,
      parts: [{ kind: "text", text: "half\n[answer cancelled here]" }] };
    expect(settleHydrated([user("u"), noteMsg("injected"), stopped])[1].btw!.state).toBe("injected");
    expect(btwFromTurn({ btw: true, btw_status: "refused", btw_reason: "rate_limited" }, "h4"))
      .toMatchObject({ state: "refused", reason: "rate_limited" });
  });
});

describe("sendBtwNote v2 body", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("sends via and client_id when given", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ ok: true, status: "queued", client_id: "abc12345" }),
      { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const res = await sendBtwNote("s", "note", "tok", { via: "voice", clientId: "abc12345" });
    expect(res.status).toBe("queued");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(
      { instruction: "note", via: "voice", client_id: "abc12345" });
  });
});
