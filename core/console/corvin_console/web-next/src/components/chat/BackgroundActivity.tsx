/**
 * Live "what is still running" strip above the composer (ADR-2236 `bg_status`).
 *
 * A turn stays open while the CLI's background children run — a shell command, a
 * Monitor, a background agent, a workflow — and until now the only sign was a
 * counter in the header. This lists them like a presence/activity panel: icon by
 * kind, the (scrubbed, server-side) description, a live clock, and a state mark.
 * It is read-only and sits OUTSIDE the composer, so it never takes focus or
 * blocks typing. Everything shown arrives in the `bg_status` event; nothing is
 * polled and nothing here can start or stop a task.
 */
import * as React from "react";
import { Bot, Check, ChevronDown, ChevronRight, Eye, Loader2, TerminalSquare, Workflow, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { BgChild } from "@/lib/chat-registry";

const KIND: Record<string, { label: string; Icon: React.ComponentType<{ className?: string }> }> = {
  bash: { label: "Shell command", Icon: TerminalSquare },
  monitor: { label: "Monitor", Icon: Eye },
  agent: { label: "Background agent", Icon: Bot },
  workflow: { label: "Workflow", Icon: Workflow },
};

/** 75 → "1:15", 3700 → "1:01:40". */
export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

/** Running children first (oldest first), then the most recent finished ones. */
export function orderChildren(children: readonly BgChild[], keepFinished = 3): BgChild[] {
  const running = children.filter((c) => c.state === "running").sort((a, b) => b.ageS - a.ageS);
  const done = children.filter((c) => c.state !== "running").slice(-keepFinished);
  return [...running, ...done];
}

export function BackgroundActivity({ children, receivedAt }: { children: readonly BgChild[]; receivedAt: number }) {
  const [open, setOpen] = React.useState(true);
  const [now, setNow] = React.useState(() => Date.now());
  const running = children.filter((c) => c.state === "running").length;
  React.useEffect(() => {
    if (running === 0) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [running]);
  const rows = React.useMemo(() => orderChildren(children), [children]);
  if (rows.length === 0) return null;
  // The server reports each child's age at the moment of the event; the clock runs on from there.
  const drift = Math.max(0, (now - receivedAt) / 1000);

  return (
    <div
      className="mx-auto mb-1.5 w-full max-w-4xl rounded-xl border border-border/70 bg-card/60 px-3 py-1.5 text-xs"
      role="status"
      aria-live="polite"
      data-testid="bg-activity"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left text-muted-foreground hover:text-foreground"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        {running > 0 ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
        <span data-testid="bg-activity-summary">
          {running > 0 ? `${running} running in the background` : "Background work finished"}
        </span>
      </button>
      {open && (
        <ul className="mt-1 space-y-0.5">
          {rows.map((c) => {
            const meta = KIND[c.kind] ?? { label: "Background task", Icon: TerminalSquare };
            const isRunning = c.state === "running";
            const failed = c.state === "failed";
            return (
              <li
                key={c.id}
                data-testid="bg-activity-row"
                data-state={c.state}
                data-kind={c.kind}
                className={cn("flex items-center gap-2", !isRunning && "text-muted-foreground")}
              >
                <meta.Icon className="h-3.5 w-3.5 shrink-0" />
                <span className="shrink-0 font-medium">{meta.label}</span>
                {c.label && <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">{c.label}</span>}
                {!c.label && <span className="flex-1" />}
                <span className="shrink-0 font-mono tabular-nums text-muted-foreground">
                  {formatElapsed(c.ageS + (isRunning ? drift : 0))}
                </span>
                {isRunning
                  ? <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-emerald-500" aria-label="running" />
                  : failed
                    ? <X className="h-3.5 w-3.5 shrink-0 text-destructive" aria-label="failed" />
                    : <Check className="h-3.5 w-3.5 shrink-0" aria-label="finished" />}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
