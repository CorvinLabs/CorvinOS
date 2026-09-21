import React, { useState } from "react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Loader2, ChevronRight } from "lucide-react";
import { formatTime, formatHealth, getStatusColor } from "@/utils/learning-loop-formatting";

interface LoopEntry {
  loop_id: string;
  plugin_id: string;
  skill_id?: string | null;
  status: "active" | "dormant" | "stale" | "degrading";
  health: { score: number; trend: "up" | "down" | "flat" };
  last_event?: string;
  event_count_7d: number;
  description?: string;
}

interface Props {
  loops: LoopEntry[];
  loading: boolean;
  onSelect: (loopId: string) => void;
}

export const LearningLoopGrid: React.FC<Props> = ({ loops, loading, onSelect }) => {
  const [sortBy, setSortBy] = useState<keyof LoopEntry>("last_event");
  const [filterStatus, setFilterStatus] = useState<string | null>(null);

  const filtered = filterStatus ? loops.filter((l) => l.status === filterStatus) : loops;
  const sorted = [...filtered].sort((a, b) => {
    if (sortBy === "last_event") {
      return new Date(b.last_event || 0).getTime() - new Date(a.last_event || 0).getTime();
    }
    if (sortBy === "health") {
      return b.health.score - a.health.score;
    }
    return String(a[sortBy as keyof LoopEntry]).localeCompare(String(b[sortBy as keyof LoopEntry]));
  });

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-2 flex-wrap">
        <Button variant={filterStatus === null ? "default" : "outline"} size="sm" onClick={() => setFilterStatus(null)}>
          All ({loops.length})
        </Button>
        {["active", "dormant", "stale", "degrading"].map((status) => {
          const count = loops.filter((l) => l.status === status).length;
          return (
            <Button
              key={status}
              variant={filterStatus === status ? "default" : "outline"}
              size="sm"
              onClick={() => setFilterStatus(status)}
            >
              {status} ({count})
            </Button>
          );
        })}
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead
              className="cursor-pointer hover:bg-muted"
              onClick={() => setSortBy("plugin_id")}
            >
              Plugin
            </TableHead>
            <TableHead
              className="cursor-pointer hover:bg-muted"
              onClick={() => setSortBy("loop_id")}
            >
              Loop ID
            </TableHead>
            <TableHead
              className="cursor-pointer hover:bg-muted"
              onClick={() => setSortBy("status")}
            >
              Status
            </TableHead>
            <TableHead
              className="cursor-pointer hover:bg-muted"
              onClick={() => setSortBy("last_event")}
            >
              Last Event
            </TableHead>
            <TableHead
              className="cursor-pointer hover:bg-muted"
              onClick={() => setSortBy("event_count_7d")}
            >
              Events/7d
            </TableHead>
            <TableHead
              className="cursor-pointer hover:bg-muted"
              onClick={() => setSortBy("health")}
            >
              Health
            </TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((loop) => (
            <TableRow key={loop.loop_id} className="cursor-pointer hover:bg-muted/60">
              <TableCell className="font-medium">{loop.plugin_id}</TableCell>
              <TableCell className="text-sm text-muted-foreground">{loop.loop_id}</TableCell>
              <TableCell>
                <Badge variant="outline" className={getStatusColor(loop.status)}>
                  {loop.status}
                </Badge>
              </TableCell>
              <TableCell className="text-sm">{formatTime(loop.last_event)}</TableCell>
              <TableCell className="text-right">{loop.event_count_7d}</TableCell>
              <TableCell>
                <div className="flex items-center gap-2">
                  <div className="h-2 w-16 rounded bg-muted">
                    <div
                      className="h-2 rounded"
                      style={{
                        width: `${loop.health.score * 100}%`,
                        background: "var(--viz-tier-2)",
                      }}
                    />
                  </div>
                  <span className="text-sm">{(loop.health.score * 100).toFixed(0)}%</span>
                  <span className={`text-xs ${loop.health.trend === "up" ? "text-emerald-600 dark:text-emerald-400" : loop.health.trend === "down" ? "text-red-600 dark:text-red-400" : "text-muted-foreground"}`}>
                    {loop.health.trend === "up" ? "↑" : loop.health.trend === "down" ? "↓" : "→"}
                  </span>
                </div>
              </TableCell>
              <TableCell>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onSelect(loop.loop_id)}
                  className="ml-auto"
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {sorted.length === 0 && (
        <div className="py-10 text-center text-sm text-muted-foreground">
          {filterStatus ? (
            `No ${filterStatus} loops.`
          ) : (
            <>
              <p className="font-medium text-foreground">No learning loops declared</p>
              <p className="mx-auto mt-2 max-w-md">
                A loop appears here once an installed plugin declares a{" "}
                <code className="rounded bg-muted px-1 py-0.5">learning_loops</code> section
                in its manifest. The index is reconciled with the installed plugins
                every five minutes; runtime metrics fill in from the learning events
                those loops emit.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
};
