/**
 * A2A audit trail — the last 100 A2A events (metadata only: origin/endpoint
 * id, event type, status, duration — never payload content), newest first.
 * Moved out of agent-hub.tsx (Console navigation refactor, Phase 3): this is
 * an audit record, so it belongs on the Compliance page next to the other
 * audit cards, not on an A2A-specific connection-management page.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Clock, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getA2ALog, type A2AEvent } from "@/lib/api/a2a";

function fmtTs(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString();
}

function fmtDuration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function SeverityDot({ severity }: { severity: string }) {
  if (severity === "CRITICAL")
    return <span className="inline-block h-2 w-2 rounded-full bg-destructive" />;
  if (severity === "WARNING")
    return <span className="inline-block h-2 w-2 rounded-full bg-amber-400" />;
  return <span className="inline-block h-2 w-2 rounded-full bg-emerald-400" />;
}

function EventTypeBadge({ type }: { type: string }) {
  const short = type.replace("A2A.", "");
  const isRejected = short.includes("rejected");
  const isSpawned = short.includes("spawned") || short.includes("sent");
  return (
    <Badge
      variant="outline"
      className={
        isRejected
          ? "border-destructive/40 text-destructive font-mono text-[10px]"
          : isSpawned
            ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-400 font-mono text-[10px]"
            : "font-mono text-[10px]"
      }
    >
      {short}
    </Badge>
  );
}

function EventRow({ event: ev }: { event: A2AEvent }) {
  const peer = ev.origin_id ?? ev.endpoint_id ?? "?";
  return (
    <div className="flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-muted/40">
      <SeverityDot severity={ev.severity} />
      <span className="w-20 shrink-0 font-mono text-xs text-muted-foreground">
        {fmtTs(ev.ts)}
      </span>
      <EventTypeBadge type={ev.event_type} />
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
        {peer}
      </span>
      {ev.status && (
        <Badge
          variant="outline"
          className={`shrink-0 text-[10px] ${ev.status === "ok" ? "text-emerald-600" : "text-destructive"}`}
        >
          {ev.status}
        </Badge>
      )}
      <span className="shrink-0 text-xs text-muted-foreground">
        {fmtDuration(ev.duration_ms)}
      </span>
    </div>
  );
}

export function A2AAuditTrail() {
  const [autoRefresh, setAutoRefresh] = React.useState(true);

  const log = useQuery({
    queryKey: ["a2a", "log"],
    queryFn: ({ signal }) => getA2ALog({ limit: 100 }, signal),
    refetchInterval: autoRefresh ? 5_000 : false,
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>A2A Audit Trail</CardTitle>
            <CardDescription>
              Last 100 A2A events (metadata only) — newest first.
              {log.data && (
                <span className="ml-2 font-mono text-xs">
                  {log.data.count} event{log.data.count !== 1 ? "s" : ""}
                </span>
              )}
            </CardDescription>
          </div>
          <Button
            variant={autoRefresh ? "secondary" : "outline"}
            size="sm"
            onClick={() => setAutoRefresh((v) => !v)}
            className="gap-1.5"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${autoRefresh ? "animate-spin" : ""}`} style={autoRefresh ? { animationDuration: "3s" } : {}} />
            {autoRefresh ? "Live" : "Paused"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {log.isLoading && (
          <div className="space-y-px p-4">
            {[1, 2, 3, 4, 5].map((n) => <Skeleton key={n} className="h-10 rounded" />)}
          </div>
        )}
        {log.isError && (
          <p className="p-4 text-sm text-destructive">Failed to load events.</p>
        )}
        {log.data && log.data.events.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-12 text-muted-foreground">
            <Clock className="h-8 w-8 opacity-30" />
            <p className="text-sm">No A2A events yet.</p>
          </div>
        )}
        {log.data && log.data.events.length > 0 && (
          <div className="divide-y divide-border">
            {log.data.events.map((ev, i) => (
              <EventRow key={i} event={ev} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
