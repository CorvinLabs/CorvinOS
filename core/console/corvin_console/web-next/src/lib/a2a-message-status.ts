/**
 * The status symbol stuck to every peer-chat message.
 *
 * One pure function maps what the feed knows about a message — its own record, the
 * reply to it, and the furthest stage the peer reported for the task — to a symbol, a
 * label and the whole chain (queued → sent → delivered → accepted → working → done).
 * Kept out of the component so every mapping is unit-testable
 * (tests/unit/a2a-message-status.test.ts): a wrong state→symbol mapping is invisible in
 * a screenshot until a user hits that case.
 *
 * Honesty rules the mapping follows:
 *  - The symbol only claims what was observed. "Working" needs a stage the PEER
 *    reported; without one the best honest statement is "Sent".
 *  - A stage is shown with the age of its last observation, never as "live".
 *  - "Unconfirmed" is shown only while no stage was ever observed; once one was, the
 *    last known stage wins.
 */
import type { A2AFeedMessage, A2AStageInfo, A2AStageName } from "@/lib/api/a2a";

export type StatusIcon = "clock" | "check" | "check-check" | "spinner" | "x" | "help";
export type StatusTone = "muted" | "info" | "working" | "success" | "warning" | "danger";
export type ChainState = "done" | "current" | "pending" | "failed";

export interface ChainStep { key: string; label: string; state: ChainState }

export interface MessageStatusView {
  /** Stable machine key (tests, data attributes). */
  key: string;
  icon: StatusIcon;
  tone: StatusTone;
  /** Short text next to the symbol. */
  label: string;
  /** Tooltip / screen-reader sentence. */
  detail: string;
  /** Outbound messages only: the whole chain with the current position. */
  chain: ChainStep[];
  /** Seconds since the shown stage was last observed (null = no observation). */
  observedAgeS: number | null;
}

export interface StatusContext {
  /** The reply record for this task, if one exists (kind "response"). */
  reply?: Pick<A2AFeedMessage, "status" | "error" | "direction"> | null;
  /** Furthest stage the peer reported for this task. */
  stage?: A2AStageInfo | null;
  /** Unix seconds "now" (injected so the mapping is deterministic). */
  now: number;
}

/** Max send timeout + slack — past it a queued message is reported as not sent. */
export const QUEUED_STALE_S = 3600 + 120;

const TERMINAL: ReadonlySet<A2AStageName> = new Set(["completed", "failed", "rejected", "timeout"]);

const CHAIN_KEYS = ["queued", "sent", "delivered", "accepted", "working", "done"] as const;
const CHAIN_LABELS: Record<(typeof CHAIN_KEYS)[number], string> = {
  queued: "Queued", sent: "Sent", delivered: "Delivered to peer",
  accepted: "Accepted by peer", working: "Agent working", done: "Done",
};

/** Index in CHAIN_KEYS that is "current"; everything before it is done. */
function chain(position: number, failedAt: number | null = null): ChainStep[] {
  return CHAIN_KEYS.map((key, i) => ({
    key, label: CHAIN_LABELS[key],
    state: failedAt === i ? "failed" : i < position ? "done" : i === position ? "current" : "pending",
  }));
}

const REASON_TEXT: Record<string, string> = {
  busy: "the peer is busy with other tasks",
  restart: "the peer restarted while working on it",
  injection: "the peer refused the message",
  gate: "a gate on the peer refused it",
  worker_error: "the peer's agent hit an error",
  timeout: "the peer's agent ran out of time",
  group_refused: "the peer did not accept the group message",
  data_flow: "the peer's data-flow gate blocked it",
  gate_error: "a safety gate on the peer failed (fail-closed) — try again later",
  egress: "the peer's network policy blocks its agent engine",
  house_rules: "the peer's acceptable-use gate refused it",
  house_rules_unavailable: "the peer's acceptable-use gate is unavailable (fail-closed)",
  quota: "the peer's compute quota is used up",
  license: "the peer's licence check failed",
  attachments: "the peer could not store the attachments",
  engine_unavailable: "the peer's agent engine could not start — check Claude Code is installed and signed in there",
  engine_failed: "the peer's agent engine failed to run it (a usage limit or sign-in problem on the peer?) — try again later",
  engine_error: "the peer's agent engine reported an error",
  refused: "the peer refused it",
};

function reasonText(reason: string | undefined): string {
  return reason && REASON_TEXT[reason] ? ` — ${REASON_TEXT[reason]}` : "";
}

function age(stage: A2AStageInfo | null | undefined, now: number): number | null {
  return stage ? Math.max(0, Math.round(now - stage.ts)) : null;
}

export function formatAge(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return `${Math.floor(seconds / 3600)} h ago`;
}

function view(p: Omit<MessageStatusView, "chain" | "observedAgeS"> & Partial<Pick<MessageStatusView, "chain" | "observedAgeS">>): MessageStatusView {
  return { chain: [], observedAgeS: null, ...p };
}

function failedOutbound(label: string, detail: string, at: number, stage?: A2AStageInfo | null, now = 0): MessageStatusView {
  return view({ key: "failed", icon: "x", tone: "danger", label, detail, chain: chain(at, at), observedAgeS: age(stage, now) });
}

/** Status of one message. Never throws; unknown shapes degrade to a neutral "sent" check. */
export function messageStatusView(m: A2AFeedMessage, ctx: StatusContext): MessageStatusView {
  const { reply, stage, now } = ctx;
  const a = age(stage, now);

  // ── a reply record ──────────────────────────────────────────────────
  if (m.kind === "response") {
    const bad = Boolean(m.error) || ["rejected", "timeout", "error"].includes(m.status);
    const who = m.direction === "in" ? "Reply received" : "Reply sent";
    if (m.status === "unconfirmed") {
      return view({ key: "reply-unconfirmed", icon: "help", tone: "warning", label: "No confirmation",
        detail: "The peer may have received the message but did not confirm it — check before resending." });
    }
    if (bad) {
      return view({ key: "reply-failed", icon: "x", tone: "danger",
        label: m.status === "rejected" ? "Refused" : m.status === "timeout" ? "Timed out" : "Failed",
        detail: m.error ? `${who} failed: ${m.error}` : `${who} failed (${m.status}).` });
    }
    return view({ key: "reply-ok", icon: "check", tone: "muted", label: m.direction === "in" ? "Replied" : "Sent", detail: `${who}.` });
  }

  // ── a message the peer sent to us ───────────────────────────────────
  if (m.direction === "in") {
    if (reply) {
      const refused = Boolean(reply.error) || ["rejected", "timeout", "error"].includes(reply.status);
      return refused
        ? view({ key: "in-refused", icon: "x", tone: "danger", label: "Refused", detail: "This instance refused the message." })
        : view({ key: "in-answered", icon: "check-check", tone: "success", label: "Answered", detail: "Received and answered by this instance." });
    }
    return view({ key: "in-received", icon: "check", tone: "muted", label: "Received", detail: "Received from the peer." });
  }

  // ── a message we sent ───────────────────────────────────────────────
  // Terminal stage reported by the peer wins over everything inferred locally.
  if (stage && TERMINAL.has(stage.stage)) {
    if (stage.stage === "completed") {
      return view({ key: "done", icon: "check-check", tone: "success", label: "Done",
        detail: `The peer finished the task (${formatAge(a)}).`, chain: chain(5), observedAgeS: a });
    }
    const label = stage.stage === "rejected" ? "Refused" : stage.stage === "timeout" ? "Timed out" : "Failed";
    return failedOutbound(label, `The peer reported the task as ${stage.stage}${reasonText(stage.reason)} (${formatAge(a)}).`, 4, stage, now);
  }

  if (reply) {
    if (reply.status === "unconfirmed") {
      // No answer came back, but a stage tells us how far it got.
      if (stage) return inFlight(stage, now, true);
      return view({ key: "unconfirmed", icon: "help", tone: "warning", label: "Delivery unconfirmed",
        detail: "The peer may have received the message but did not confirm it — check before resending.",
        chain: chain(1) });
    }
    const refused = Boolean(reply.error) || ["rejected", "timeout", "error"].includes(reply.status);
    if (refused) {
      const label = reply.status === "rejected" ? "Refused" : reply.status === "timeout" ? "Timed out" : "Failed";
      return failedOutbound(label, reply.error ? `${label}: ${reply.error}` : `The peer answered ${reply.status}.`, 3);
    }
    return view({ key: "done", icon: "check-check", tone: "success", label: "Done", detail: "The peer answered.", chain: chain(5) });
  }

  if (["rejected", "timeout", "error"].includes(m.status) || m.error) {
    const label = m.status === "rejected" ? "Refused" : m.status === "timeout" ? "Timed out" : "Failed";
    return failedOutbound(label, m.error ? `${label}: ${m.error}` : `The message ${m.status}.`, 1);
  }
  if (m.status === "unconfirmed") {
    if (stage) return inFlight(stage, now, true);
    return view({ key: "unconfirmed", icon: "help", tone: "warning", label: "Delivery unconfirmed",
      detail: "The peer may have received the message but did not confirm it — check before resending.", chain: chain(1) });
  }
  if (m.status === "queued") {
    if (now - m.ts > QUEUED_STALE_S) {
      return failedOutbound("Not sent", "The message was queued but never sent (interrupted) — send it again.", 0);
    }
    return view({ key: "queued", icon: "clock", tone: "muted", label: "Queued", detail: "Waiting to be sent.", chain: chain(0) });
  }
  if (stage) return inFlight(stage, now, false);
  return view({ key: "sent", icon: "check", tone: "muted", label: "Sent",
    detail: "Sent. The peer has not reported progress yet.", chain: chain(1) });
}

function inFlight(stage: A2AStageInfo, now: number, unconfirmed: boolean): MessageStatusView {
  const a = age(stage, now);
  const when = `last seen ${formatAge(a)}`;
  const tail = unconfirmed ? " No final answer arrived — this is the last known state." : "";
  switch (stage.stage) {
    case "delivered":
      return view({ key: "delivered", icon: "check-check", tone: "info", label: "Delivered",
        detail: `The peer received it (${when}).${tail}`, chain: chain(2), observedAgeS: a });
    case "accepted":
      return view({ key: "accepted", icon: "check-check", tone: "info", label: "Accepted",
        detail: `The peer accepted it and is about to start (${when}).${tail}`, chain: chain(3), observedAgeS: a });
    default:
      return view({ key: "working", icon: "spinner", tone: "working", label: "Agent working",
        detail: `The peer's agent is working on it (${when}).${tail}`, chain: chain(4), observedAgeS: a });
  }
}
