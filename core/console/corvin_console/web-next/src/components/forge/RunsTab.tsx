/**
 * Run history across all four Forges (ADR-2253): one table from GET /forge/runs, filters by kind and status, and a
 * row opens the same Production feed the generators show. Nothing is invented: a duration, model or artifact the
 * record lacks reads "—" / "unknown", and an unreachable API says "not available", never an empty-but-fine table.
 */
import { Fragment, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import RunFeed from "@/components/forge/RunFeed";
import { listRuns, retryToolRun, type RunSummary } from "@/lib/api/forge-runs";
import { artifactLink, durationSeconds, engineLabel, formatDuration } from "@/lib/forge-run-feed";

const KINDS = ["", "skill", "tool", "plugin", "layer"] as const;
const STATUSES = ["", "running", "success", "failed"] as const;

function when(ts?: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "—" : d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

export default function RunsTab() {
  const { session } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [kind, setKind] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [open, setOpen] = useState<string | null>(null);

  const runs = useQuery({
    queryKey: ["forge-runs", kind, status],
    queryFn: ({ signal }) => listRuns({ kind: kind || undefined, status: status || undefined }, signal),
    refetchInterval: (q) => (q.state.data?.runs.some((r) => r.status === "running") ? 3000 : false),
  });

  const retry = useMutation({
    mutationFn: (id: string) => retryToolRun(id, session?.csrf_token ?? ""),
    onSuccess: (r) => {
      // The Tool generator re-attaches to the run id it finds here, so the retry is visible where it belongs.
      try { window.sessionStorage.setItem("forge-creator-run:tool", r.run_id); } catch { /* the run is just not re-attached */ }
      void qc.invalidateQueries({ queryKey: ["forge-runs"] });
      navigate("/app/forge?tab=generator&sub=tool");
    },
  });

  return (
    <section className="space-y-3 p-4" data-testid="forge-runs">
      <header className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-medium">Runs</h3>
        <label className="text-xs text-muted-foreground">
          Kind{" "}
          <select aria-label="Kind" data-testid="runs-kind" value={kind} onChange={(e) => setKind(e.target.value)}
            className="rounded border border-border bg-background px-1 py-0.5 text-foreground">
            {KINDS.map((k) => <option key={k} value={k}>{k === "" ? "All" : k}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted-foreground">
          Status{" "}
          <select aria-label="Status" data-testid="runs-status" value={status} onChange={(e) => setStatus(e.target.value)}
            className="rounded border border-border bg-background px-1 py-0.5 text-foreground">
            {STATUSES.map((s) => <option key={s} value={s}>{s === "" ? "All" : s}</option>)}
          </select>
        </label>
        <span className="ml-auto text-[11px] text-muted-foreground" data-testid="runs-count">
          {runs.data ? `${runs.data.count} run${runs.data.count === 1 ? "" : "s"}` : ""}
        </span>
      </header>

      {runs.isError && (
        <p className="text-sm text-muted-foreground" data-testid="runs-unavailable">Run history is not available on this build.</p>
      )}
      {runs.data && runs.data.runs.length === 0 && (
        <p className="text-sm text-muted-foreground" data-testid="runs-empty">
          {kind || status ? "No run matches these filters." : "No runs yet. Forge something from the Generator tab."}
        </p>
      )}

      {runs.data && runs.data.runs.length > 0 && (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="py-1 pr-3 font-normal">Started</th><th className="pr-3 font-normal">Kind</th>
              <th className="pr-3 font-normal">Status</th><th className="pr-3 font-normal">Duration</th>
              <th className="pr-3 font-normal">Engine · model</th><th className="pr-3 font-normal">Artifact</th>
              <th className="font-normal" />
            </tr>
          </thead>
          <tbody>
            {runs.data.runs.map((r: RunSummary) => {
              const link = r.artifact_ref ? artifactLink(r.artifact_ref.kind) : null;
              return (
                <Fragment key={r.run_id}>
                  <tr className="border-t border-border/50" data-testid="run-row" data-run-id={r.run_id} data-kind={r.kind} data-status={r.status}>
                    <td className="py-1 pr-3 tabular-nums">{when(r.started_at)}</td>
                    <td className="pr-3">{r.kind}{r.operation ? ` · ${r.operation}` : ""}</td>
                    <td className="pr-3">
                      <Badge variant={r.status === "failed" ? "danger" : "outline"} className="text-[10px]">{r.status}</Badge>
                    </td>
                    <td className="pr-3 tabular-nums">{r.status === "running" ? "running" : formatDuration(durationSeconds(r.started_at, r.ended_at))}</td>
                    <td className="pr-3 font-mono">{engineLabel(r.engine, r.model)}</td>
                    <td className="pr-3 font-mono">
                      {r.artifact_ref?.id ? (link ? <Link to={link} className="text-accent underline">{r.artifact_ref.id}</Link> : r.artifact_ref.id) : "—"}
                    </td>
                    <td className="space-x-1 whitespace-nowrap text-right">
                      <Button size="sm" variant="ghost" className="h-6 px-2 text-[11px]" data-testid="run-open"
                        onClick={() => setOpen(open === r.run_id ? null : r.run_id)}>
                        {open === r.run_id ? "Hide" : "Details"}
                      </Button>
                      {r.retryable && (
                        <Button size="sm" variant="outline" className="h-6 px-2 text-[11px]" data-testid="run-retry"
                          disabled={retry.isPending || !session} onClick={() => retry.mutate(r.run_id)}>
                          Retry
                        </Button>
                      )}
                    </td>
                  </tr>
                  {open === r.run_id && (
                    <tr><td colSpan={7} className="pb-3"><RunFeed runId={r.run_id} running={r.status === "running"} /></td></tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
      {retry.isError && (
        <p role="alert" className="text-sm text-destructive" data-testid="runs-retry-error">
          {retry.error instanceof Error ? retry.error.message : "The retry could not be started."}
        </p>
      )}
    </section>
  );
}
