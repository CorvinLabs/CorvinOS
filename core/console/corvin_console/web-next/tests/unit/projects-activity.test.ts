import { describe, expect, it } from "vitest";
import type { ActivityAgent, ActivityEvent, ActivitySnapshot } from "@/lib/api/projects";
import { INITIAL, MAX_ROWS, announcement, cursor, elapsed, emptyFeed, filterRows, reduce, statusLine, toolSentence, workingAgents, type State } from "@/pages/projects/activity-logic";

const E = (seq: number, kind: ActivityEvent["kind"], more: Partial<ActivityEvent> = {}, ts = 1000 + seq): ActivityEvent =>
  ({ seq, id: `9:${seq}`, ts, kind, session_id: "c1", ...more });
const snap = (over: Partial<ActivitySnapshot> = {}): ActivitySnapshot => ({ epoch: "9", seq: 0, oldest_seq: 1, reset: false, gap: false, events: [], agents: [], watcher: "running", note: "", ...over });
const agent = (state: ActivityAgent["state"], over: Partial<ActivityAgent> = {}): ActivityAgent => ({ session_id: "c1", title: "Plan", state, since: 1000, tool: null, detail: "", tools: 0, ...over });
const feed = (s: State, msgs: ActivityEvent[]): State => msgs.reduce((acc, e) => reduce(acc, { type: "activity", data: e }), s);

describe("words for a tool call", () => {
  it("says what happened in plain words from the safe detail", () => {
    expect(toolSentence("Bash", "pytest")).toBe("Ran pytest");
    expect(toolSentence("Bash", "")).toBe("Ran a shell command");
    expect(toolSentence("Edit", "invoice.py")).toBe("Edited invoice.py");
    expect(toolSentence("Write", "")).toBe("Wrote a file");
    expect(toolSentence("WebFetch", "example.com")).toBe("Fetched example.com");
    expect(toolSentence("WebSearch", "")).toBe("Searched the web");
    expect(toolSentence("Task", "")).toBe("Handed work to a sub-agent");
    expect(toolSentence("mcp__corvin_orchestration__acs_delegate", "")).toBe("Used the corvin orchestration tool");
    expect(toolSentence("Read", "a.py", true)).toBe("Sub-agent: read a.py");
    expect(toolSentence("", "")).toBe("Used a tool");
  });
});

describe("the feed", () => {
  it("is newest first, deduplicated by seq, and never repeats a tail after a reconnect", () => {
    let s = feed(INITIAL, [E(1, "turn_started", { title: "Plan" }), E(2, "tool_use", { name: "Read", detail: "a.py" })]);
    expect(s.rows.map((r) => r.text)).toEqual(["Read a.py", 'Started working in "Plan"']);
    s = feed(s, [E(2, "tool_use", { name: "Read", detail: "a.py" }), E(1, "turn_started")]);
    expect(s.rows.length).toBe(2);
    expect(s.lastSeq).toBe(2);
  });
  it("collapses the same sentence of the same chat within 5 s into x N, but never two different files", () => {
    let s = feed(INITIAL, [E(1, "tool_use", { name: "Read", detail: "a.py" }, 1000), E(2, "tool_use", { name: "Read", detail: "a.py" }, 1002), E(3, "tool_use", { name: "Read", detail: "a.py" }, 1004)]);
    expect(s.rows.length).toBe(1);
    expect(s.rows[0].count).toBe(3);
    s = feed(s, [E(4, "tool_use", { name: "Read", detail: "b.py" }, 1005)]);
    expect(s.rows.map((r) => [r.text, r.count])).toEqual([["Read b.py", 1], ["Read a.py", 3]]);
    s = feed(s, [E(5, "tool_use", { name: "Read", detail: "b.py" }, 1100)]);                 // too late to collapse
    expect(s.rows.length).toBe(3);
  });
  it("a burst of 30 Read calls on one file shows at most 5 rows", () => {
    const evs = Array.from({ length: 30 }, (_, i) => E(i + 1, "tool_use", { name: "Read", detail: "same.py" }, 1000 + i * 0.1));
    expect(feed(INITIAL, evs).rows.length).toBeLessThanOrEqual(5);
  });
  it("shows a file change once per file: a later change to the same file replaces the row", () => {
    const s = feed(INITIAL, [E(1, "file_changed", { path: "a.py", status: "modified", additions: 1, deletions: 0 }, 1000), E(2, "file_changed", { path: "b.py", status: "untracked" }, 1001),
      E(3, "file_changed", { path: "a.py", status: "modified", additions: 4, deletions: 1 }, 1003)]);
    expect(s.rows.map((r) => r.text)).toEqual(["Changed a.py +4 -1", "New file b.py"]);
  });
  it("is bounded to 300 rows", () => {
    const evs = Array.from({ length: MAX_ROWS + 40 }, (_, i) => E(i + 1, "tool_use", { name: "Read", detail: `f${i}.py` }, 1000 + i * 60));
    expect(feed(INITIAL, evs).rows.length).toBe(MAX_ROWS);
  });
  it("tells an error stop from a clean finish and a closed chat", () => {
    const s = feed(INITIAL, [E(1, "turn_finished", { reason: "done" }), E(2, "turn_finished", { reason: "error" }), E(3, "turn_finished", { reason: "closed" })]);
    expect(s.rows.map((r) => r.text)).toEqual(["Stopped (the chat was closed)", "Stopped with an error", "Finished"]);
    expect(s.rows[1].tone).toBe("bad");
  });
  it("filters tools (with turns) and files", () => {
    const s = feed(INITIAL, [E(1, "turn_started"), E(2, "tool_use", { name: "Bash", detail: "git" }), E(3, "file_changed", { path: "x", status: "modified" })]);
    expect(filterRows(s.rows, "tools").map((r) => r.kind)).toEqual(["tool", "turn"]);
    expect(filterRows(s.rows, "files").map((r) => r.kind)).toEqual(["file"]);
    expect(filterRows(s.rows, "all").length).toBe(3);
  });
});

describe("agents follow the events, not only the snapshots (found live: the card appeared 15 s late)", () => {
  it("a turn start makes the chat a working agent at once, a tool call updates it, a finish makes it idle", () => {
    let s = feed(INITIAL, [E(1, "turn_started", { title: "Fix VAT" }, 2000)]);
    expect(s.agents).toHaveLength(1);
    expect([s.agents[0].state, s.agents[0].title, s.agents[0].since, s.agents[0].tools]).toEqual(["working", "Fix VAT", 2000, 0]);
    s = feed(s, [E(2, "tool_use", { name: "Bash", detail: "pytest" }, 2001), E(3, "tool_use", { name: "Edit", detail: "a.py" }, 2002)]);
    expect([s.agents[0].tool, s.agents[0].detail, s.agents[0].tools]).toEqual(["Edit", "a.py", 2]);
    s = feed(s, [E(4, "turn_finished", { reason: "done" }, 2003)]);
    expect([s.agents[0].state, s.agents[0].tool, s.agents[0].ok, s.agents[0].finished]).toEqual(["idle", null, true, 2003]);
    s = feed(s, [E(5, "turn_started", { title: "Fix VAT" }, 2010), E(6, "turn_finished", { reason: "error" }, 2011)]);
    expect([s.agents[0].state, s.agents[0].ok]).toEqual(["idle", false]);
    expect(s.agents).toHaveLength(1);                                                        // the same chat is one agent
  });
  it("two chats are two agents; an event of an unknown chat's tool does not invent one", () => {
    const s = feed(INITIAL, [E(1, "turn_started", { title: "A", session_id: "a" } as never), E(2, "turn_started", { title: "B", session_id: "b" } as never)]);
    expect(s.agents.map((a) => a.session_id).sort()).toEqual(["a", "b"]);
    expect(feed(INITIAL, [E(1, "tool_use", { name: "Read", session_id: "ghost" } as never)]).agents).toEqual([]);
  });
  it("a snapshot is authoritative: it replaces what the events built", () => {
    const s = feed(INITIAL, [E(1, "turn_started", { title: "A" })]);
    expect(reduce(s, { type: "snapshot", data: snap({ seq: 1, agents: [] }) }).agents).toEqual([]);
  });
});

describe("snapshots, restarts and gaps", () => {
  it("a snapshot sets agents and watcher, fills the backlog and the cursor", () => {
    const s = reduce(INITIAL, { type: "snapshot", data: snap({ seq: 2, events: [E(1, "turn_started"), E(2, "tool_use", { name: "Bash", detail: "git" })], agents: [agent("working")] }) });
    expect([s.conn, s.epoch, s.lastSeq, s.rows.length, workingAgents(s.agents).length]).toEqual(["live", "9", 2, 2, 1]);
    expect(cursor(s)).toBe("9:2");
    expect(cursor(INITIAL)).toBeNull();
  });
  it("a different epoch drops the feed and says the console restarted; the new backlog follows", () => {
    let s = reduce(INITIAL, { type: "snapshot", data: snap({ seq: 3, events: [E(1, "turn_started"), E(2, "turn_finished"), E(3, "turn_started")] }) });
    s = reduce(s, { type: "snapshot", data: { ...snap({ epoch: "10", seq: 1, events: [{ ...E(1, "turn_started"), id: "10:1" }] }) } });
    expect([s.restarted, s.epoch, s.lastSeq, s.rows.length]).toEqual([true, "10", 1, 1]);
  });
  it("a server-flagged reset does the same even for the first snapshot of a stale cursor", () => {
    const s = reduce(INITIAL, { type: "snapshot", data: snap({ reset: true, seq: 1, events: [E(1, "turn_started")] }) });
    expect(s.restarted).toBe(true);
  });
  it("remembers that events were missed until the epoch changes", () => {
    let s = reduce(INITIAL, { type: "snapshot", data: snap({ gap: true }) });
    expect(s.gap).toBe(true);
    s = reduce(s, { type: "snapshot", data: snap({ gap: false }) });
    expect(s.gap).toBe(true);
  });
  it("a removed project ends the view", () => {
    expect(reduce(INITIAL, { type: "end", reason: "project_removed" }).conn).toBe("gone");
    expect(reduce(INITIAL, { type: "end", reason: "lifetime" }).conn).toBe("connecting");      // a normal rollover changes nothing
  });
});

describe("status, empty states and announcements", () => {
  it("the status line is words, and names a paused watcher and a failed connection", () => {
    expect(statusLine({ ...INITIAL, conn: "live" })).toEqual({ text: "Live", tone: "ok" });
    expect(statusLine({ ...INITIAL, conn: "live", watcher: "paused" }).text).toContain("paused");
    expect(statusLine({ ...INITIAL, conn: "reconnecting" }).tone).toBe("wait");
    expect(statusLine({ ...INITIAL, conn: "error" }).text).toContain("Could not connect");
    expect(statusLine({ ...INITIAL, conn: "gone" }).tone).toBe("bad");
  });
  it("the empty feed says why", () => {
    expect(emptyFeed({ ...INITIAL, conn: "live" }, "all")).toContain("No agent has worked");
    expect(emptyFeed({ ...INITIAL, conn: "live" }, "files")).toContain("noticed the next time");
    expect(emptyFeed({ ...INITIAL, conn: "error" }, "all")).toContain("connection is down");
  });
  it("announces only the start or the finish of an agent, not every row", () => {
    expect(announcement([], [agent("working")])).toBe("Corvin started working in Plan");
    expect(announcement([agent("working")], [agent("working", { tools: 5 })])).toBe("");
    expect(announcement([agent("working")], [agent("idle")])).toBe("Corvin finished in Plan");
    expect(announcement([agent("idle")], [agent("idle")])).toBe("");
  });
  it("elapsed time in plain units", () => {
    expect(elapsed(1000, 1_012_000)).toBe("12 s");
    expect(elapsed(1000, 1_180_000)).toBe("3 min");
    expect(elapsed(1000, 1_000_000 + 7_300_000)).toBe("2 h");
  });
});
