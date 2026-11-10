/**
 * The explorer's decisions, pure and unit-tested (CONCEPT-0128 sections 4-6, REVIEW-0060): how the stream's messages become agents,
 * a bounded feed and a status line; how rows collapse so the eye is not flooded; what a restart or a gap does to the feed.
 */
import type { ActivityAgent, ActivityEvent, ActivitySnapshot } from "@/lib/api/projects";

export const MAX_ROWS = 300;
export const COLLAPSE_WINDOW_S = 5;
export const FILE_MERGE_WINDOW_S = 10;

export type RowKind = "turn" | "tool" | "file";
export interface Row {
  key: string;
  kind: RowKind;
  ts: number;
  session_id: string;
  text: string;
  count: number;
  path?: string;
  links?: { id: string; kind: string }[];
  sub?: boolean;
  tone?: "plain" | "bad";
}

export type Conn = "connecting" | "live" | "reconnecting" | "error" | "gone";
export interface State {
  epoch: string | null;
  lastSeq: number;
  agents: ActivityAgent[];
  rows: Row[];                         // newest first
  restarted: boolean;
  gap: boolean;
  watcher: "off" | "running" | "paused";
  conn: Conn;
  titles: Record<string, string>;
}

export const INITIAL: State = { epoch: null, lastSeq: 0, agents: [], rows: [], restarted: false, gap: false, watcher: "off", conn: "connecting", titles: {} };

const FILE_VERB: Record<string, string> = { read: "Read", edit: "Edited", multiedit: "Edited", write: "Wrote", notebookedit: "Edited" };

/** What a tool call says in words. `detail` is already the safe, derived string (program, file name, host) or empty. */
export function toolSentence(name: string, detail: string, sub = false): string {
  const n = (name || "").toLowerCase().replace(/_/g, "");
  let s: string;
  if (n === "bash") s = detail ? `Ran ${detail}` : "Ran a shell command";
  else if (n in FILE_VERB) s = detail ? `${FILE_VERB[n]} ${detail}` : `${FILE_VERB[n]} a file`;
  else if (n === "webfetch") s = detail ? `Fetched ${detail}` : "Fetched a web page";
  else if (n === "websearch") s = "Searched the web";
  else if (n === "grep" || n === "glob") s = "Searched the files";
  else if (n === "task" || n === "agent") s = "Handed work to a sub-agent";
  else if (n === "todowrite") s = "Updated its to-do list";
  else if (n.startsWith("mcp")) s = `Used the ${(name.split("__")[1] || "connected").replace(/_/g, " ")} tool`;
  else s = name ? `Used ${name}` : "Used a tool";
  return sub ? `Sub-agent: ${s.charAt(0).toLowerCase()}${s.slice(1)}` : s;
}

function fileText(e: ActivityEvent): string {
  const st = e.status === "untracked" || e.status === "added" ? "New file" : e.status === "deleted" ? "Deleted" : e.status === "renamed" ? "Renamed" : "Changed";
  const counts = e.additions != null || e.deletions != null ? ` +${e.additions ?? 0} -${e.deletions ?? 0}` : "";
  return `${st} ${e.path ?? ""}${counts}`.trim();
}

function rowFor(e: ActivityEvent, titles: Record<string, string>): Row | null {
  const base = { key: e.id, ts: e.ts, session_id: e.session_id ?? "", count: 1 };
  switch (e.kind) {
    case "turn_started": return { ...base, kind: "turn", text: `Started working${e.title ? ` in "${e.title}"` : ""}` };
    case "turn_finished": return { ...base, kind: "turn", text: e.reason === "error" ? "Stopped with an error" : e.reason === "closed" ? "Stopped (the chat was closed)" : "Finished", tone: e.reason === "error" ? "bad" : "plain" };
    case "tool_use": return { ...base, kind: "tool", text: toolSentence(e.name ?? "", e.detail ?? "", !!e.sub), sub: !!e.sub, path: e.name };
    case "file_changed": return { ...base, kind: "file", text: fileText(e), path: e.path, links: e.links };
    case "file_settled": return { ...base, kind: "file", text: `Settled ${e.path ?? ""}`.trim(), path: e.path };
    case "files_burst": return { ...base, kind: "file", text: `${e.count ?? "Many"} files changed at once (a build or a checkout)` };
  }
  void titles;
  return null;
}

/** Add one row: same tool + same chat within the window collapses into "x N"; the same file within the window is updated, not repeated. */
export function addRow(rows: Row[], row: Row): Row[] {
  const top = rows[0];
  if (top && row.kind === "tool" && top.kind === "tool" && top.session_id === row.session_id && top.path === row.path && !!top.sub === !!row.sub
      && row.ts - top.ts <= COLLAPSE_WINDOW_S && toolBase(top.text) === toolBase(row.text)) {
    return [{ ...top, ts: row.ts, count: top.count + 1, key: row.key }, ...rows.slice(1)];
  }
  if (row.kind === "file" && row.path) {
    const i = rows.findIndex((r) => r.kind === "file" && r.path === row.path);
    if (i >= 0 && i < 20 && row.ts - rows[i].ts <= FILE_MERGE_WINDOW_S) {
      return [row, ...rows.slice(0, i), ...rows.slice(i + 1)].slice(0, MAX_ROWS);
    }
  }
  return [row, ...rows].slice(0, MAX_ROWS);
}

/** "Read" of two different files must NOT collapse into one row of the first file's name: the collapse key is the whole sentence. */
function toolBase(text: string): string { return text; }

export type Msg =
  | { type: "snapshot"; data: ActivitySnapshot }
  | { type: "activity"; data: ActivityEvent }
  | { type: "end"; reason: string }
  | { type: "conn"; conn: Conn };

/** The agents follow the events at once; a snapshot (every reconnect) replaces them with the server's truth. Waiting for the next snapshot
 *  made the "Working" card appear up to 10 s late - found on the live console. */
export function agentsAfter(agents: ActivityAgent[], e: ActivityEvent): ActivityAgent[] {
  const sid = e.session_id;
  if (!sid) return agents;
  const i = agents.findIndex((a) => a.session_id === sid);
  if (e.kind === "turn_started") {
    const fresh: ActivityAgent = { session_id: sid, title: e.title ?? "", state: "working", since: e.ts, tool: null, detail: "", tools: 0 };
    return i >= 0 ? agents.map((a, k) => (k === i ? fresh : a)) : [...agents, fresh];
  }
  if (i < 0) return agents;                                          // an event of a chat we never saw start: do not invent an agent
  const a = agents[i];
  if (e.kind === "tool_use") return agents.map((x, k) => (k === i ? { ...a, tool: e.name ?? null, detail: e.detail ?? "", tools: a.tools + 1 } : x));
  if (e.kind === "turn_finished") return agents.map((x, k) => (k === i ? { ...a, state: "idle", tool: null, detail: "", finished: e.ts, ok: e.reason === "done" } : x));
  return agents;
}

function applyEvent(s: State, e: ActivityEvent): State {
  if (e.seq <= s.lastSeq) return s;                                  // already seen (a reconnect repeats the tail)
  const titles = e.session_id && e.title ? { ...s.titles, [e.session_id]: e.title } : s.titles;
  const row = rowFor(e, titles);
  return { ...s, lastSeq: e.seq, titles, agents: agentsAfter(s.agents, e), rows: row ? addRow(s.rows, row) : s.rows };
}

export function reduce(s: State, m: Msg): State {
  switch (m.type) {
    // "gone" (the project was removed from the list) is final: the reconnect that follows ends in a 404/CLOSED and must not
    // turn the status into a generic connection error
    case "conn": return s.conn === "gone" ? s : { ...s, conn: m.conn };
    case "end": return m.reason === "project_removed" ? { ...s, conn: "gone" } : s;
    case "activity": return applyEvent({ ...s, conn: "live" }, m.data);
    case "snapshot": {
      const d = m.data;
      const changedEpoch = s.epoch !== null && s.epoch !== d.epoch;
      let next: State = { ...s, epoch: d.epoch, agents: d.agents, watcher: d.watcher, conn: "live", gap: s.gap || d.gap };
      if (d.reset || changedEpoch) next = { ...next, rows: [], lastSeq: 0, restarted: true, gap: false };
      for (const e of d.events) next = { ...applyEvent(next, e), agents: next.agents };            // the backlog fills the feed; the agents stay the server's
      if (d.seq > next.lastSeq && d.events.length === 0) next = { ...next, lastSeq: d.seq };
      return next;
    }
  }
}

export const cursor = (s: State): string | null => (s.epoch ? `${s.epoch}:${s.lastSeq}` : null);
export const workingAgents = (a: ActivityAgent[]): ActivityAgent[] => a.filter((x) => x.state === "working");
export const anyWorking = (a: ActivityAgent[]): boolean => a.some((x) => x.state === "working");

export type Filter = "all" | "tools" | "files";
export function filterRows(rows: Row[], f: Filter): Row[] {
  return f === "all" ? rows : rows.filter((r) => (f === "tools" ? r.kind === "tool" || r.kind === "turn" : r.kind === "file"));
}

/** "12 s", "3 min", "2 h" - the elapsed time of a working agent. */
export function elapsed(sinceUnix: number, nowMs: number = Date.now()): string {
  const s = Math.max(0, Math.round(nowMs / 1000 - sinceUnix));
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min` : `${Math.floor(s / 3600)} h`;
}

/** The status line: words first, never colour alone. */
export function statusLine(s: State): { text: string; tone: "ok" | "wait" | "bad" } {
  if (s.conn === "gone") return { text: "This project was removed from the list", tone: "bad" };
  if (s.conn === "error") return { text: "Could not connect (the console did not answer, or too many explorers are open)", tone: "bad" };
  if (s.conn === "reconnecting") return { text: "Reconnecting...", tone: "wait" };
  if (s.conn === "connecting") return { text: "Connecting...", tone: "wait" };
  if (s.watcher === "paused") return { text: "Live - file watching paused, the console is busy", tone: "wait" };
  return { text: "Live", tone: "ok" };
}

/** The one-line empty reason for the feed. */
export function emptyFeed(s: State, f: Filter): string {
  if (s.conn === "error" || s.conn === "gone") return "Nothing to show while the connection is down.";
  if (f === "files") return "No file has changed since you opened the explorer. Changes made while it was closed are noticed the next time it opens.";
  return "No agent has worked on this project since the console started. Start a chat with this project attached and ask for something.";
}

/** What a screen reader hears: only the start or finish of an agent, never every row. */
export function announcement(prev: ActivityAgent[], next: ActivityAgent[]): string {
  const was = new Map(prev.map((a) => [a.session_id, a.state]));
  for (const a of next) {
    if (a.state === "working" && was.get(a.session_id) !== "working") return `Corvin started working${a.title ? ` in ${a.title}` : ""}`;
    if (a.state === "idle" && was.get(a.session_id) === "working") return `Corvin finished${a.title ? ` in ${a.title}` : ""}`;
  }
  return "";
}
