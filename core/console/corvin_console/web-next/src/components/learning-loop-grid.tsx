import React, { useMemo, useState } from "react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Loader2, ChevronRight } from "lucide-react";
import { formatTime, getStatusColor } from "@/utils/learning-loop-formatting";
import { ORIGIN_LABEL, type LoopEntry, type LoopOrigin } from "@/types/learning-loops";

interface Props {
  loops: LoopEntry[];
  loading: boolean;
  onSelect: (loopId: string) => void;
  selected?: string | null;
}

type SortKey = "plugin" | "loop_id" | "status" | "last_event" | "events" | "health";

const STATUSES = ["active", "dormant", "stale", "degrading", "unknown"] as const;

/**
 * Health cell.
 *
 * A null score means nothing measured this loop's health — it is rendered as a
 * dash with the reason, never as an empty bar. An empty bar reads as zero, and
 * "not measured" and "failing everything" are the opposite diagnosis.
 */
const HealthCell: React.FC<{ loop: LoopEntry }> = ({ loop }) => {
  const score = loop.health.score;
  if (score === null || score === undefined) {
    return (
      <span className="text-sm text-muted-foreground" title="No outcome or grade has been recorded for this loop">
        not measured
      </span>
    );
  }
  const trend = loop.health.trend;
  return (
    <div className="flex items-center gap-2" title={loop.health.basis || undefined}>
      <div className="h-2 w-16 rounded bg-muted">
        <div
          className="h-2 rounded"
          style={{ width: `${score * 100}%`, background: "var(--viz-tier-2)" }}
        />
      </div>
      <span className="text-sm tabular-nums">{(score * 100).toFixed(0)}%</span>
      <span
        className={
          trend === "up"
            ? "text-xs text-emerald-600 dark:text-emerald-400"
            : trend === "down"
              ? "text-xs text-red-600 dark:text-red-400"
              : "text-xs text-muted-foreground"
        }
      >
        {trend === "up" ? "↑" : trend === "down" ? "↓" : "→"}
      </span>
    </div>
  );
};

export const LearningLoopGrid: React.FC<Props> = ({ loops, loading, onSelect, selected }) => {
  const [sortBy, setSortBy] = useState<SortKey>("events");
  const [filterStatus, setFilterStatus] = useState<string | null>(null);
  const [filterOrigin, setFilterOrigin] = useState<LoopOrigin | null>(null);

  const sorted = useMemo(() => {
    let rows = loops;
    if (filterStatus) rows = rows.filter((l) => l.status === filterStatus);
    if (filterOrigin) rows = rows.filter((l) => (l.origin ?? "plugin") === filterOrigin);

    const total = (l: LoopEntry) => l.event_count_total ?? l.event_count_7d;
    return [...rows].sort((a, b) => {
      switch (sortBy) {
        case "last_event":
          return new Date(b.last_event || 0).getTime() - new Date(a.last_event || 0).getTime();
        case "health": {
          // A loop with no measured score sorts last, not as a zero — otherwise
          // "not measured" outranks a genuinely failing loop in the bad direction.
          const av = a.health.score ?? -1;
          const bv = b.health.score ?? -1;
          return bv - av;
        }
        case "events":
          return total(b) - total(a);
        case "plugin":
          return (a.skill_id || a.plugin_id).localeCompare(b.skill_id || b.plugin_id);
        case "status":
          return a.status.localeCompare(b.status);
        default:
          return a.loop_id.localeCompare(b.loop_id);
      }
    });
  }, [loops, filterStatus, filterOrigin, sortBy]);

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const origins = Array.from(new Set(loops.map((l) => (l.origin ?? "plugin") as LoopOrigin)));

  const sortable = (key: SortKey, label: string, className = "") => (
    <TableHead
      className={`cursor-pointer select-none hover:bg-muted ${className}`}
      onClick={() => setSortBy(key)}
      aria-sort={sortBy === key ? "descending" : "none"}
    >
      {label}
      {sortBy === key && <span className="ml-1 text-muted-foreground">▾</span>}
    </TableHead>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Button variant={filterStatus === null ? "default" : "outline"} size="sm" onClick={() => setFilterStatus(null)}>
          All ({loops.length})
        </Button>
        {STATUSES.map((status) => {
          const count = loops.filter((l) => l.status === status).length;
          if (!count) return null;
          return (
            <Button
              key={status}
              variant={filterStatus === status ? "default" : "outline"}
              size="sm"
              onClick={() => setFilterStatus(filterStatus === status ? null : status)}
            >
              {status} ({count})
            </Button>
          );
        })}
      </div>

      {origins.length > 1 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-muted-foreground">Source</span>
          <Button variant={filterOrigin === null ? "default" : "outline"} size="sm" onClick={() => setFilterOrigin(null)}>
            All
          </Button>
          {origins.map((origin) => (
            <Button
              key={origin}
              variant={filterOrigin === origin ? "default" : "outline"}
              size="sm"
              onClick={() => setFilterOrigin(filterOrigin === origin ? null : origin)}
            >
              {ORIGIN_LABEL[origin]} ({loops.filter((l) => (l.origin ?? "plugin") === origin).length})
            </Button>
          ))}
        </div>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            {sortable("plugin", "Loop")}
            {sortable("status", "Status")}
            {sortable("last_event", "Last event")}
            {sortable("events", "Events", "text-right")}
            {sortable("health", "Health")}
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((loop) => {
            const origin = (loop.origin ?? "plugin") as LoopOrigin;
            const total = loop.event_count_total ?? loop.event_count_7d;
            return (
              <TableRow
                key={loop.loop_id}
                onClick={() => onSelect(loop.loop_id)}
                className={`cursor-pointer hover:bg-muted/60 ${
                  selected === loop.loop_id ? "bg-muted/60" : ""
                }`}
              >
                <TableCell>
                  <div className="font-medium">{loop.skill_id || loop.plugin_id}</div>
                  <div className="text-xs text-muted-foreground">
                    {ORIGIN_LABEL[origin]} · {loop.loop_id}
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className={getStatusColor(loop.status)}>
                    {loop.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-sm">{formatTime(loop.last_event)}</TableCell>
                <TableCell className="text-right text-sm tabular-nums">
                  {total.toLocaleString("en-US")}
                  <div className="text-xs text-muted-foreground">{loop.event_count_7d} in 7d</div>
                </TableCell>
                <TableCell>
                  <HealthCell loop={loop} />
                </TableCell>
                <TableCell>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Open ${loop.loop_id}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(loop.loop_id);
                    }}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      {sorted.length === 0 && (
        <div className="py-10 text-center text-sm text-muted-foreground">
          {filterStatus || filterOrigin ? (
            "No loops match this filter."
          ) : (
            <>
              <p className="font-medium text-foreground">No learning loops found</p>
              <p className="mx-auto mt-2 max-w-md">
                Loops appear here from two sources: the OS skills and pipeline stages this
                install records learning events for, and any installed plugin that declares a{" "}
                <code className="rounded bg-muted px-1 py-0.5">learning_loops</code> section
                in its manifest.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
};
