import { describe, expect, it } from "vitest";
import type { A2AFeedMessage } from "@/lib/api";
import {
  plainPreview,
  failureHint,
  maxSeq,
  initials,
  isEmptyDelivery,
  mediaKind,
  mergeMessages,
  messageBody,
  pendingTaskIds,
  sanitizeAttachmentName,
  statusTone,
} from "@/lib/a2a-feed";

function msg(p: Partial<A2AFeedMessage>): A2AFeedMessage {
  return {
    id: p.id ?? Math.random().toString(36),
    seq: p.seq,
    ts: p.ts ?? 1,
    direction: p.direction ?? "out",
    kind: p.kind ?? "task",
    peer_id: p.peer_id ?? "peer",
    peer_label: null,
    task_id: p.task_id ?? "t1",
    status: p.status ?? "sent",
    text: p.text ?? "",
    data: p.data ?? {},
    attachments: p.attachments ?? [],
    duration_ms: null,
    error: p.error ?? null,
  };
}

describe("mediaKind", () => {
  it("renders passive media inline", () => {
    expect(mediaKind({ name: "a.png", mime: "image/png" })).toBe("image");
    expect(mediaKind({ name: "a.bin", mime: "audio/mpeg" })).toBe("audio");
    expect(mediaKind({ name: "clip.webm", mime: "" })).toBe("video");
    expect(mediaKind({ name: "r.pdf", mime: "application/pdf" })).toBe("pdf");
    expect(mediaKind({ name: "d.csv", mime: "text/csv" })).toBe("text");
  });
  it("never classifies script-capable types as inline media", () => {
    expect(mediaKind({ name: "x.svg", mime: "image/svg+xml" })).toBe("file");
    expect(mediaKind({ name: "x.png", mime: "image/svg+xml" })).toBe("file");
    expect(mediaKind({ name: "x.html", mime: "text/html" })).toBe("file");
  });
});

describe("messageBody", () => {
  it("uses the task text", () => {
    expect(messageBody({ text: "do it", data: {} }).text).toBe("do it");
  });
  it("lifts a conventional prose key out of response data", () => {
    const b = messageBody({ text: "", data: { answer: "42", confidence: 0.9 } });
    expect(b.text).toBe("42");
    expect(b.rest).toEqual({ confidence: 0.9 });
  });
  it("leaves pure structured data as rest", () => {
    expect(messageBody({ text: "", data: { n: 1 } })).toEqual({ text: "", rest: { n: 1 } });
  });
});

describe("pendingTaskIds / isEmptyDelivery", () => {
  it("marks tasks without a response as pending", () => {
    const ms = [
      msg({ task_id: "a" }),
      msg({ task_id: "a", kind: "response", direction: "in", status: "ok", data: { text: "hi" } }),
      msg({ task_id: "b" }),
    ];
    expect([...pendingTaskIds(ms)]).toEqual(["b"]);
  });
  it("flags a contentless delivered response, not an error", () => {
    expect(isEmptyDelivery(msg({ kind: "response", status: "ok" }))).toBe(true);
    expect(isEmptyDelivery(msg({ kind: "response", status: "timeout", error: "timed out" }))).toBe(false);
    expect(isEmptyDelivery(msg({ kind: "task" }))).toBe(false);
    // A refusal is not a delivery — it must never read as "delivered, empty".
    expect(isEmptyDelivery(msg({ kind: "response", status: "rejected" }))).toBe(false);
  });
  it("explains a detail-less rejection from the right side", () => {
    const r = msg({ kind: "response", status: "rejected" });
    expect(failureHint(r, false)).toMatch(/peer refused/);
    expect(failureHint(r, true)).toMatch(/Your instance refused/);
    expect(failureHint(msg({ kind: "response", status: "ok" }), false)).toBeNull();
    // Round 7: a busy agent is not a policy refusal.
    expect(failureHint(msg({ kind: "response", status: "rejected", data: { reason: "busy" } }), false))
      .toMatch(/busy/);
    // Round 8: the receiving side's own record must reach the "mine" wording
    // (older records carried error "busy" rather than data.reason).
    expect(failureHint(msg({ kind: "response", status: "rejected", data: { reason: "busy" } }), true))
      .toMatch(/Your instance was busy/);
    expect(failureHint(msg({ kind: "response", status: "rejected", error: "busy" }), true))
      .toMatch(/Your instance was busy/);
    expect(failureHint(msg({ kind: "response", status: "rejected", error: "boom" }), true)).toBeNull();
  });
});

describe("mergeMessages", () => {
  it("orders by append seq, not wall clock (a slow writer's earlier ts lands later)", () => {
    const fast = msg({ id: "f", ts: 10, seq: 1 } as Partial<A2AFeedMessage>);
    const slow = msg({ id: "s", ts: 5, seq: 2 } as Partial<A2AFeedMessage>);
    expect(mergeMessages([fast], [slow]).map((m) => m.id)).toEqual(["f", "s"]);
    expect(maxSeq([fast, slow])).toBe(2);
    expect(maxSeq([], 7)).toBe(7);
    // A malformed seq never poisons the cursor.
    expect(maxSeq([msg({ id: "x", seq: NaN } as Partial<A2AFeedMessage>)], 3)).toBe(3);
  });
  it("dedupes by id and keeps time order", () => {
    const a = msg({ id: "1", ts: 1 });
    const b = msg({ id: "2", ts: 3 });
    const c = msg({ id: "3", ts: 2 });
    expect(mergeMessages([a, b], [b, c]).map((m) => m.id)).toEqual(["1", "3", "2"]);
    const known = [a];
    expect(mergeMessages(known, [a])).toBe(known);
  });
});

describe("helpers", () => {
  it("statusTone", () => {
    expect(statusTone("ok")).toBe("ok");
    expect(statusTone("filtered")).toBe("warn");
    expect(statusTone("rejected")).toBe("error");
  });
  it("initials", () => {
    expect(initials("corvin_a2a_2")).toBe("C2");
    expect(initials("gpu")).toBe("GP");
    // Ten agents must not all read "A0".
    expect(new Set(Array.from({ length: 10 }, (_, i) => initials(`agent-${String(i + 1).padStart(2, "0")}`))).size).toBe(10);
    expect(initials("gpu server")).toBe("GS");
  });
  it("plainPreview strips Markdown", () => {
    expect(plainPreview("**agent-02** received `e2e-1` [link](http://x) ![img](y)")).toBe("agent-02 received e2e-1 link img");
  });
  it("sanitizeAttachmentName satisfies the protocol name rule", () => {
    const re = /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$/;
    for (const n of ["../etc/passwd", ".ssh", "Bildschirmfoto 2026-09-25 um 10.00.png", "ä.png", "a..b"]) {
      const s = sanitizeAttachmentName(n);
      expect(s).toMatch(re);
      expect(s).not.toContain("..");
    }
  });
});

describe("peer markdown images", () => {
  it("only inline data:/blob: images render; same-origin URLs never auto-load", async () => {
    const { isInlineImageUrl } = await import("@/components/markdown");
    expect(isInlineImageUrl("data:image/png;base64,AAAA")).toBe(true);
    expect(isInlineImageUrl("blob:http://x/1")).toBe(true);
    // Same-origin GET with the operator's cookies (e.g. local-login) — blocked.
    expect(isInlineImageUrl("/v1/console/auth/local-login")).toBe(false);
    expect(isInlineImageUrl("https://tracker.example/p.png")).toBe(false);
    expect(isInlineImageUrl("data:text/html,<script>")).toBe(false);
  });
});
