/**
 * What a run changed: a unified diff between two snapshots of the artifact (ADR-2253 D3). For a refine it is the previous
 * version against the result; otherwise the last two iterations. Rendered from GET /forge/runs/{id}/diff.
 */
import { useEffect, useState } from "react";
import { getRunDiff, getRunSnapshots, type RunDiff as RunDiffData } from "@/lib/api/forge-runs";
import { chooseDiff, classifyDiffLines } from "@/lib/forge-run-feed";

const LINE_CLASS = {
  add: "text-emerald-700 dark:text-emerald-300",
  del: "text-destructive",
  hunk: "text-muted-foreground",
  meta: "text-muted-foreground",
  context: "",
} as const;

export default function RunDiff({ runId, running, refreshKey }: { runId: string; running: boolean; refreshKey: number }) {
  const [diff, setDiff] = useState<RunDiffData | null>(null);
  const [state, setState] = useState<"loading" | "none" | "ready" | "error">("loading");

  useEffect(() => {
    const ctl = new AbortController();
    let stopped = false;
    (async () => {
      try {
        const { snapshots } = await getRunSnapshots(runId, ctl.signal);
        const choice = chooseDiff(snapshots);
        if (stopped) return;
        if (!choice) { setDiff(null); setState("none"); return; }
        const d = await getRunDiff(runId, choice.from, choice.to, ctl.signal);
        if (stopped) return;
        setDiff(d);
        setState("ready");
      } catch (e) {
        if (stopped || (e instanceof DOMException && e.name === "AbortError")) return;
        setState("error");
      }
    })();
    return () => { stopped = true; ctl.abort(); };
    // refreshKey changes when the feed received new events (a new snapshot may exist); `running` flips once at the end
  }, [runId, running, refreshKey]);

  if (state === "loading") return null;
  if (state === "error") return null; // the feed above already tells the operator when the API is unreachable
  if (state === "none") {
    return running ? null : (
      <p className="text-[11px] text-muted-foreground" data-testid="run-diff-none">
        No earlier snapshot to compare with.
      </p>
    );
  }
  const changed = (diff?.files ?? []).filter((f) => f.status !== "same");
  return (
    <div className="space-y-2" data-testid="run-diff">
      <p className="text-[11px] text-muted-foreground">
        Changes: <span data-testid="run-diff-range">{diff?.from.label} → {diff?.to.label}</span>
      </p>
      {changed.length === 0 && <p className="text-xs text-muted-foreground" data-testid="run-diff-identical">The two snapshots are identical.</p>}
      {changed.map((f) => (
        <details key={f.path} open data-testid="run-diff-file" data-path={f.path} data-status={f.status}>
          <summary className="cursor-pointer select-none font-mono text-[11px]">{f.path} <span className="text-muted-foreground">({f.status})</span></summary>
          <pre className="mt-1 max-h-72 overflow-auto rounded border border-border/60 bg-background p-2 font-mono text-[11px] leading-snug">
            {classifyDiffLines(f.diff).map((l, i) => (
              <div key={i} data-diff={l.kind} className={LINE_CLASS[l.kind]}>{l.text || " "}</div>
            ))}
          </pre>
        </details>
      ))}
    </div>
  );
}
