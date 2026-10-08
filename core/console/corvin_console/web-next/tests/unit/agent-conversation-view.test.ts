import { describe, expect, it } from "vitest";
import {
  DEFAULT_SETTINGS, audienceLabel, clampInt, eventText, settingsDiff, sideOf, timeline,
} from "@/lib/agent-conversation-view";
import type { ConversationMessage } from "@/lib/api/federation";

const msg = (seq: number, speaker: ConversationMessage["speaker"], extra = {}): ConversationMessage => ({
  seq, ts: seq, speaker, agent_id: speaker, address: null, task_id: null, status: "ok",
  text: `t${seq}`, duration_ms: 0, ...extra,
});

describe("timeline", () => {
  it("interleaves messages and system events by the moderator's single seq", () => {
    const rows = timeline({
      messages: [msg(1, "operator"), msg(2, "local"), msg(4, "peer")],
      events: [{ seq: 3, ts: 3, event: "paused" }],
    });
    expect(rows.map((r) => `${r.kind}${r.seq}`)).toEqual(["message1", "message2", "event3", "message4"]);
  });
  it("tolerates a server that sends no events", () => {
    expect(timeline({ messages: [msg(1, "operator")], events: undefined as never })).toHaveLength(1);
  });
});

describe("sides", () => {
  it("puts the operator and your agent on the right, the peer on the left", () => {
    expect(sideOf(msg(1, "operator"))).toBe("mine");
    expect(sideOf(msg(1, "local"))).toBe("mine");
    expect(sideOf(msg(1, "peer"))).toBe("peer");
  });
});

describe("audienceLabel", () => {
  const names = { local: "opus", peer: "haiku" };
  it("says who a private note was for — and nothing for agents", () => {
    expect(audienceLabel(msg(1, "operator", { target: "peer" }), names)).toBe("to haiku only");
    expect(audienceLabel(msg(1, "operator", { target: "local" }), names)).toBe("to opus only");
    expect(audienceLabel(msg(1, "operator", { target: null }), names)).toBe("to both agents");
    expect(audienceLabel(msg(1, "operator"), names)).toBe("to both agents");
    expect(audienceLabel(msg(1, "local", { target: "peer" }), names)).toBeNull();
  });
});

describe("settings", () => {
  it("sends only what changed", () => {
    const next = { ...DEFAULT_SETTINGS, pace_s: 10 };
    expect(settingsDiff(DEFAULT_SETTINGS, next)).toEqual({ pace_s: 10 });
    expect(settingsDiff(DEFAULT_SETTINGS, DEFAULT_SETTINGS)).toEqual({});
    const notes = { ...DEFAULT_SETTINGS, role_notes: { local: "terse", peer: "" } };
    expect(settingsDiff(DEFAULT_SETTINGS, notes)).toEqual({ role_notes: { local: "terse", peer: "" } });
  });
  it("clamps into the server's range", () => {
    expect(clampInt(9999, [50, 600])).toBe(600);
    expect(clampInt(-3, [0, 60])).toBe(0);
    expect(clampInt(NaN, [50, 600])).toBe(50);
  });
  it("describes system rows", () => {
    expect(eventText({ seq: 1, ts: 1, event: "paused" })).toBe("Paused");
    expect(eventText({ seq: 1, ts: 1, event: "settings", settings: { ...DEFAULT_SETTINGS, max_words: 90 } }))
      .toContain("90 words");
  });
});
