import * as React from "react";
import { useAuth } from "@/lib/auth";
import {
  browserCreateSession, browserClose, browserNavigate, browserObserve,
  browserClick, browserFill, browserScroll, browserActions, browserConfirm,
  browserPause, browserAgent, browserAgentStop,
  type BrowserObservation, type BrowserAction, type BrowserPending,
} from "@/lib/api";

/**
 * Browser Automation live view (ADR-0182 M3). The user watches the agent-driven
 * browser as a live image, sees every action in real time, approves/declines
 * sensitive actions, and can pause / take over.
 */
export function BrowserPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";

  const [sid, setSid] = React.useState<string | null>(null);
  const [url, setUrl] = React.useState("https://example.com");
  const [obs, setObs] = React.useState<BrowserObservation | null>(null);
  const [actions, setActions] = React.useState<BrowserAction[]>([]);
  const [pending, setPending] = React.useState<BrowserPending[]>([]);
  const [paused, setPaused] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [task, setTask] = React.useState("");
  const sinceRef = React.useRef(0);
  const frameRef = React.useRef<HTMLImageElement | null>(null);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null); setBusy(true);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const start = () => run(async () => {
    const { session: s } = await browserCreateSession(csrf);
    setSid(s); sinceRef.current = 0; setActions([]);
  });

  const stop = () => run(async () => {
    if (sid) await browserClose(sid, csrf);
    setSid(null); setObs(null); setActions([]); setPending([]);
  });

  const go = () => sid && run(async () => setObs(await browserNavigate(sid, url, csrf)));
  const observe = () => sid && run(async () => setObs(await browserObserve(sid, csrf)));
  const click = (i: number) => sid && run(async () => { await browserClick(sid, i, csrf); setObs(await browserObserve(sid, csrf)); });
  const fill = (i: number) => {
    const text = window.prompt("Text to type into element " + i + ":");
    if (text != null && sid) run(() => browserFill(sid, i, text, csrf));
  };
  const scroll = (d: string) => sid && run(() => browserScroll(sid, d, csrf));
  const confirm = (id: string, approved: boolean) => sid && run(() => browserConfirm(sid, id, approved, csrf));
  const togglePause = () => sid && run(async () => { await browserPause(sid, !paused, csrf); setPaused(!paused); });
  const runAgent = () => sid && task.trim() && run(async () => { await browserAgent(sid, task.trim(), csrf); });
  const stopAgent = () => sid && run(() => browserAgentStop(sid, csrf));

  // Poll the live frame (screencast) + the action log while a session is open.
  React.useEffect(() => {
    if (!sid) return;
    let alive = true;
    const tickFrame = () => {
      if (!alive || !frameRef.current) return;
      frameRef.current.src = `/v1/console/browser/${sid}/frame.jpg?t=${Date.now()}`;
    };
    const tickLog = async () => {
      if (!alive) return;
      try {
        const r = await browserActions(sid, sinceRef.current);
        sinceRef.current = r.next;
        if (r.actions.length) setActions((a) => [...a, ...r.actions].slice(-300));
        setPending(r.pending);
      } catch { /* transient */ }
    };
    const f = window.setInterval(tickFrame, 700);
    const l = window.setInterval(tickLog, 800);
    tickFrame(); tickLog();
    return () => { alive = false; window.clearInterval(f); window.clearInterval(l); };
  }, [sid]);

  return (
    <div className="p-4 space-y-4 max-w-6xl">
      <div>
        <h1 className="text-lg font-semibold">Browser</h1>
        <p className="text-xs text-muted-foreground">
          The agent drives a real browser — navigate, fill, click. You see every action live and
          can pause or take over. Sensitive actions (buy / send / delete / login) ask for your OK.
        </p>
      </div>

      {!sid ? (
        <button onClick={start} disabled={busy}
          className="rounded bg-primary text-primary-foreground text-sm px-3 py-1.5">
          Start browser session
        </button>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <input value={url} onChange={(e) => setUrl(e.target.value)}
            className="flex-1 min-w-[240px] rounded border border-border bg-background px-2 py-1 text-sm"
            placeholder="https://…" onKeyDown={(e) => e.key === "Enter" && go()} />
          <button onClick={go} disabled={busy} className="rounded bg-primary text-primary-foreground text-sm px-3 py-1.5">Go</button>
          <button onClick={observe} disabled={busy} className="rounded border border-border text-sm px-2 py-1.5">Observe</button>
          <button onClick={() => scroll("down")} disabled={busy} className="rounded border border-border text-sm px-2 py-1.5">Scroll ↓</button>
          <button onClick={togglePause} disabled={busy}
            className={`rounded text-sm px-3 py-1.5 ${paused ? "bg-amber-500 text-white" : "border border-border"}`}>
            {paused ? "Resume (you have control)" : "Pause / Take over"}
          </button>
          <button onClick={stop} disabled={busy} className="rounded border border-destructive text-destructive text-sm px-2 py-1.5">Close</button>
        </div>
      )}

      {error && <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>}

      {sid && (
        <div className="rounded border border-primary/40 bg-primary/5 p-3 space-y-2">
          <p className="text-sm font-medium">Give the browser a task</p>
          <p className="text-[11px] text-muted-foreground">
            Type a note in plain language — the agent drives the browser step by step
            (you watch the window + log). Sensitive actions ask you first.
          </p>
          <div className="flex gap-2">
            <input value={task} onChange={(e) => setTask(e.target.value)}
              className="flex-1 rounded border border-border bg-background px-2 py-1 text-sm"
              placeholder='e.g. "go to news.ycombinator.com and read the top story title"'
              onKeyDown={(e) => e.key === "Enter" && runAgent()} />
            <button onClick={runAgent} disabled={busy || !task.trim()}
              className="rounded bg-primary text-primary-foreground text-sm px-3 py-1.5">Run</button>
            <button onClick={stopAgent} disabled={busy}
              className="rounded border border-border text-sm px-2 py-1.5">Stop</button>
          </div>
        </div>
      )}

      {pending.length > 0 && (
        <div className="rounded border border-amber-500 bg-amber-500/10 p-3 space-y-2">
          <p className="text-sm font-medium">Confirm sensitive action</p>
          {pending.map((p) => (
            <div key={p.id} className="flex items-center justify-between gap-3 text-sm">
              <span>{p.action} “{p.name}” on {p.host}</span>
              <span className="flex gap-2">
                <button onClick={() => confirm(p.id, true)} className="rounded bg-emerald-600 text-white px-2 py-1 text-xs">Approve</button>
                <button onClick={() => confirm(p.id, false)} className="rounded bg-destructive text-white px-2 py-1 text-xs">Decline</button>
              </span>
            </div>
          ))}
        </div>
      )}

      {sid && (
        <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-4">
          {/* Live view */}
          <div className="rounded border border-border bg-black/80 min-h-[420px] flex items-center justify-center overflow-hidden">
            {/* eslint-disable-next-line jsx-a11y/alt-text */}
            <img ref={frameRef} className="max-w-full" alt="live browser view" />
          </div>
          {/* Elements + action log */}
          <div className="space-y-3">
            {obs && (
              <div className="rounded border border-border p-2">
                <p className="text-xs font-medium mb-1 truncate">{obs.title} — {obs.marks.length} elements</p>
                <div className="max-h-48 overflow-auto text-xs space-y-0.5">
                  {obs.marks.map((m) => (
                    <div key={m.index} className="flex items-center justify-between gap-2">
                      <span className="truncate">[{m.index}] {m.role}: {m.name}</span>
                      <span className="flex gap-1 shrink-0">
                        <button onClick={() => click(m.index)} className="text-primary hover:underline">click</button>
                        {(m.role === "textbox" || m.role === "combobox") &&
                          <button onClick={() => fill(m.index)} className="text-primary hover:underline">fill</button>}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="rounded border border-border p-2">
              <p className="text-xs font-medium mb-1">Action log</p>
              <div className="max-h-64 overflow-auto text-[11px] font-mono space-y-0.5">
                {actions.slice().reverse().map((a, i) => (
                  <div key={i} className="text-muted-foreground">
                    {a.action}{a.host ? ` · ${a.host}` : ""}{a.name ? ` · ${a.name}` : ""}
                    {a.ok === false ? " · ✗" : ""}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
