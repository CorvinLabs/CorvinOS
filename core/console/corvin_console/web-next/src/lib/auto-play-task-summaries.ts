/**
 * Which task recap (if any) should be read aloud automatically while Voice is on.
 *
 * The per-turn voice already speaks a reply live, in the chat the operator is
 * looking at. A task's persisted recap exists for the OTHER case: the task
 * finished while the operator was elsewhere (another chat, a reload, a closed
 * tab). So a recap is auto-played only if
 *   - it has not been heard (by auto-play or by the operator pressing play),
 *   - its task finished after auto-play was first active here (no replay of the
 *     whole back catalogue the day this ships), and
 *   - the task did NOT finish right around a reply this tab already spoke live
 *     (that would be the same content twice).
 * They play oldest first, one at a time; a pile-up beyond MAX_QUEUE keeps only
 * the newest — the rest stay in the Voice Summaries library.
 *
 * Times are SERVER epoch seconds throughout; the browser clock is only used to
 * translate "now" through the offset the server's own `now` provides.
 */
import type { TaskVoiceSummary } from "@/lib/api";

/** A recap whose task finished this close to a live-spoken reply was already heard. */
export const LIVE_WINDOW_S = 90;
export const MAX_QUEUE = 5;

export const summaryKey = (s: Pick<TaskVoiceSummary, "sid" | "task_id">): string =>
  `${s.sid}:${s.task_id}`;

const doneAt = (s: TaskVoiceSummary): number => s.completed_at ?? s.created_at ?? 0;

export interface AutoPlayInput {
  summaries: TaskVoiceSummary[];
  heard: ReadonlySet<string>;
  /** Tasks that finished before this (server epoch s) are never auto-played. */
  sinceS: number;
  /** When (server epoch s) this tab last spoke a reply of this chat live. */
  liveSpokenS: number | null;
}

export interface AutoPlayDecision {
  /** The recap to start now, or null. */
  play: TaskVoiceSummary | null;
  /** Keys to remember as heard — includes `play`, the live-covered and the skipped. */
  markHeard: string[];
}

export function decideAutoPlay(input: AutoPlayInput): AutoPlayDecision {
  const markHeard: string[] = [];
  const open = input.summaries
    .filter((s) => s.task_id && s.audio_url && !input.heard.has(summaryKey(s)) && doneAt(s) >= input.sinceS)
    .sort((a, b) => doneAt(a) - doneAt(b));

  const pending: TaskVoiceSummary[] = [];
  for (const s of open) {
    if (input.liveSpokenS != null && Math.abs(doneAt(s) - input.liveSpokenS) <= LIVE_WINDOW_S) {
      markHeard.push(summaryKey(s)); // the reply was spoken live — don't say it twice
    } else {
      pending.push(s);
    }
  }
  for (const s of pending.slice(0, Math.max(0, pending.length - MAX_QUEUE))) {
    markHeard.push(summaryKey(s)); // pile-up: only the newest MAX_QUEUE are worth waiting for
  }
  const queue = pending.slice(-MAX_QUEUE);
  const play = queue[0] ?? null;
  if (play) markHeard.push(summaryKey(play));
  return { play, markHeard };
}

/**
 * The recap to show as text in the chat right now: the newest one that carries
 * text, finished after this pane was opened (older ones live in the Voice
 * Summaries library) and was not dismissed. Independent of whether its audio
 * exists, so a failed speech synthesis still shows the summary.
 */
export function pickLiveSummary(input: {
  summaries: TaskVoiceSummary[];
  sinceS: number;
  dismissed: ReadonlySet<string>;
}): TaskVoiceSummary | null {
  const open = input.summaries
    .filter((s) => s.task_id && s.text.trim() && doneAt(s) >= input.sinceS
      && !input.dismissed.has(summaryKey(s)))
    .sort((a, b) => doneAt(b) - doneAt(a));
  return open[0] ?? null;
}

// ── persistence (localStorage) + in-memory live marker ─────────────────────────
const HEARD_KEY = "corvin.voice.heardTaskSummaries";
const SINCE_KEY = "corvin.voice.autoplaySince";
const HEARD_CAP = 500;

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function loadHeard(): Set<string> {
  const arr = readJson<unknown>(HEARD_KEY, []);
  return new Set(Array.isArray(arr) ? arr.filter((k): k is string => typeof k === "string") : []);
}

export function rememberHeard(keys: string[]): void {
  if (keys.length === 0) return;
  const merged = [...loadHeard(), ...keys];
  try {
    localStorage.setItem(HEARD_KEY, JSON.stringify([...new Set(merged)].slice(-HEARD_CAP)));
  } catch {
    /* storage full / blocked: worst case a recap plays once more */
  }
}

/** Server time (epoch s) at which auto-play was first active in this browser. */
export function getOrInitSince(serverNowS: number): number {
  const stored = readJson<number | null>(SINCE_KEY, null);
  if (typeof stored === "number") return stored;
  try {
    localStorage.setItem(SINCE_KEY, JSON.stringify(serverNowS));
  } catch {
    /* ignore */
  }
  return serverNowS;
}

let serverOffsetS = 0;
const liveSpoken = new Map<string, number>();

/** server clock minus browser clock, from the latest listing response. */
export function setServerOffset(serverNowS: number): void {
  serverOffsetS = serverNowS - Date.now() / 1000;
}

/** Called when this tab speaks a reply of `sid` live (the per-turn voice). */
export function noteLiveSpoken(sid: string): void {
  liveSpoken.set(sid, Date.now() / 1000 + serverOffsetS);
}

export function getLiveSpoken(sid: string): number | null {
  return liveSpoken.get(sid) ?? null;
}
