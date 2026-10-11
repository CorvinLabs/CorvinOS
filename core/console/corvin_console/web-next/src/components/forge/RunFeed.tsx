/**
 * "Production" feed under a generator: what the Forge is doing and has done, from the run's event log
 * (/forge/runs/{id}/events). Live while the run is going, complete afterwards, re-attached after a reload because it is
 * keyed only by the run id. A run that has no event log (it predates it) says so instead of looking empty by accident.
 */
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api/client";
import { getRunEvents, type RunEvent } from "@/lib/api/forge-runs";
import {
  clock, droppedCount, filesWritten, groupByPhase, iterationRows, mergeEvents, summarizeFindings, testSummary,
} from "@/lib/forge-run-feed";

const POLL_MS = 1500;

export default function RunFeed({ runId, running }: { runId: string; running: boolean }) {
  useAuth(); // the api client carries the session; this keeps the hook order stable with the other panels
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [missing, setMissing] = useState(false);
  const after = useRef(0);
  const lastRunId = useRef(runId);

  // A different run starts from an empty feed.
  if (lastRunId.current !== runId) {
    lastRunId.current = runId;
    after.current = 0;
    setEvents([]);
    setMissing(false);
  }

  useEffect(() => {
    let stopped = false;
    const ctl = new AbortController();
    const pull = async () => {
      try {
        const page = await getRunEvents(runId, after.current, ctl.signal);
        if (stopped) return;
        if (page.events.length > 0) {
          after.current = page.next_after;
          setEvents((cur) => mergeEvents(cur, page.events));
        }
        setMissing(false);
      } catch (e) {
        if (stopped || (e instanceof DOMException && e.name === "AbortError")) return;
        if (e instanceof ApiError && e.status === 404) setMissing(true); // unknown run: nothing to show, nothing invented
        // any other error is transient (console restarting): the next tick asks again
      }
    };
    void pull();
    // While running poll; once finished do one more pull (the final events land with the status flip), then stop.
    const timer = running ? window.setInterval(() => void pull(), POLL_MS) : window.setTimeout(() => void pull(), 400);
    return () => { stopped = true; ctl.abort(); window.clearInterval(timer); window.clearTimeout(timer); };
  }, [runId, running]);

  const groups = groupByPhase(events);
  const findings = summarizeFindings(events);
  const files = filesWritten(events);
  const tests = testSummary(events);
  const iterations = iterationRows(events);
  const dropped = droppedCount(events);

  return (
    <section className="space-y-3 rounded-md border border-border/60 bg-muted/20 p-3" data-testid="run-feed" aria-label="Production">
      <header className="flex items-center justify-between">
        <h4 className="text-xs font-medium">Production</h4>
        <span className="text-[10px] text-muted-foreground" data-testid="run-feed-count">
          {events.length} event{events.length === 1 ? "" : "s"}{running ? " · live" : ""}
        </span>
      </header>

      {missing && (
        <p className="text-xs text-muted-foreground" data-testid="run-feed-missing">
          No event log for this run (it ran before the log existed, or it was removed).
        </p>
      )}
      {!missing && events.length === 0 && (
        <p className="text-xs text-muted-foreground" data-testid="run-feed-empty">
          {running ? "Waiting for the first event…" : "This run recorded no events."}
        </p>
      )}

      {tests && (
        <p className="text-xs" data-testid="run-feed-tests">
          Sandbox tests: {tests.passed ?? "unknown"} of {tests.total ?? "unknown"} passed
        </p>
      )}
      {iterations.length > 0 && (
        <table className="text-xs" data-testid="run-feed-iterations">
          <thead><tr className="text-left text-muted-foreground"><th className="pr-4 font-normal">Iterations</th><th className="font-normal">Quality</th></tr></thead>
          <tbody>
            {iterations.map((r) => (
              <tr key={r.seq}><td className="pr-4 tabular-nums">{r.iterations ?? "unknown"}</td><td className="tabular-nums">{r.quality ?? "unknown"}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {files.length > 0 && (
        <div data-testid="run-feed-files">
          <p className="text-[11px] text-muted-foreground">Files written ({files.length})</p>
          <ul className="font-mono text-[11px]">{files.map((f) => <li key={f}>{f}</li>)}</ul>
        </div>
      )}
      {findings.length > 0 && (
        <div data-testid="run-feed-findings">
          <p className="text-[11px] text-muted-foreground">Review findings by dimension</p>
          <ul className="text-xs">
            {findings.map((f) => (
              <li key={f.dimension} data-dimension={f.dimension}>
                <span className="font-medium">{f.dimension}</span>
                {" — "}{f.confirmed} confirmed · {f.plausible} plausible · {f.refuted} refuted
              </li>
            ))}
          </ul>
        </div>
      )}

      {groups.map((g, i) => (
        <details key={`${g.phase}-${i}`} open={running && i === groups.length - 1} className="text-[11px]" data-testid="run-feed-phase" data-phase={g.phase}>
          <summary className="cursor-pointer select-none font-medium">
            {g.phase} <span className="font-normal text-muted-foreground">({g.events.length})</span>
          </summary>
          <ol className="mt-1 space-y-0.5 font-mono">
            {g.events.map((e) => (
              <li key={e.seq} data-type={e.type} data-level={e.level} className={e.level === "error" ? "text-destructive" : undefined}>
                <span className="text-muted-foreground">{e.seq} {clock(e.ts)}</span> [{e.type}] {e.message}
              </li>
            ))}
          </ol>
        </details>
      ))}

      {dropped > 0 && (
        <p className="text-[11px] text-muted-foreground" data-testid="run-feed-truncated">
          {dropped} event{dropped === 1 ? " was" : "s were"} left out to keep the log small.
        </p>
      )}
    </section>
  );
}
