import React from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Loader2, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDetail } from "@/hooks/use-learning-loops-detail";
import { formatTime, getStatusColor } from "@/utils/learning-loop-formatting";
import { ORIGIN_LABEL, type LoopEntry, type LoopOrigin, type TrendPoint } from "@/types/learning-loops";

const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`;

/**
 * Daily activity and, where it was measured, the day's outcome success rate.
 *
 * Two measures of different scale, so they are small multiples over one shared
 * date domain rather than a dual axis (ADR-0761). A day with no recorded
 * outcome draws no health mark at all — a gap, not a zero, because a quiet day
 * did not fail. A non-zero count always draws at least a sliver, so a day with
 * one event never disappears into the baseline.
 */
const TrendChart: React.FC<{ points: TrendPoint[] }> = ({ points }) => {
  if (points.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        No dated activity for this loop. Its source store does not timestamp its records.
      </p>
    );
  }

  const maxCount = Math.max(...points.map((p) => p.event_count), 1);
  const withScore = points.filter((p) => p.health_score !== null);

  const axis = (
    <div className="mt-2 flex justify-between text-xs text-muted-foreground">
      <span>{points[0].date}</span>
      <span>{points[points.length - 1].date}</span>
    </div>
  );

  return (
    <div className="space-y-6">
      <div>
        <p className="mb-3 text-xs uppercase text-muted-foreground">
          Events per day · {points.length} days
        </p>
        <div className="flex h-24 items-end gap-1">
          {points.map((p) => (
            <div
              key={p.date}
              className="flex-1 rounded-t"
              style={{
                height: p.event_count === 0 ? "2px" : `${Math.max((p.event_count / maxCount) * 100, 4)}%`,
                background: p.event_count === 0 ? "var(--viz-baseline)" : "var(--viz-tier-2)",
              }}
              title={`${p.date}: ${p.event_count} events`}
            />
          ))}
        </div>
        {axis}
      </div>

      <div>
        <p className="mb-3 text-xs uppercase text-muted-foreground">
          Outcome success rate · days with no recorded outcome are blank
        </p>
        <div className="flex h-24 items-end gap-1">
          {points.map((p) => (
            <div
              key={p.date}
              className="flex-1 rounded-t"
              style={{
                height: p.health_score === null ? "2px" : `${Math.max(p.health_score * 100, 4)}%`,
                background: p.health_score === null ? "var(--viz-baseline)" : "var(--viz-tier-1)",
              }}
              title={
                p.health_score === null
                  ? `${p.date}: no outcome recorded`
                  : `${p.date}: ${pct(p.health_score)} of outcomes succeeded`
              }
            />
          ))}
        </div>
        {axis}
        {withScore.length === 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            No outcomes were recorded in this window, so there is no success rate to show.
          </p>
        )}
      </div>
    </div>
  );
};

export const LearningLoopDetail: React.FC<{ loop: LoopEntry }> = ({ loop }) => {
  const { trend, events, recommendations, loading, error } = useDetail(loop.loop_id);
  const origin = (loop.origin ?? "plugin") as LoopOrigin;

  const exportJSON = () => {
    const blob = new Blob([JSON.stringify({ loop, trend, events }, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${loop.loop_id.replace(/[^a-z0-9._-]+/gi, "_")}-export.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const scored = (trend?.points ?? []).filter((p) => p.health_score !== null);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardTitle className="flex flex-wrap items-center gap-2">
                {loop.skill_id || loop.plugin_id}
                <Badge variant="outline" className={getStatusColor(loop.status)}>
                  {loop.status}
                </Badge>
                <Badge variant="outline">{ORIGIN_LABEL[origin]}</Badge>
              </CardTitle>
              <p className="mt-2 text-sm text-muted-foreground">{loop.loop_id}</p>
            </div>
            <Button variant="outline" size="sm" onClick={exportJSON}>
              <Download className="mr-2 h-4 w-4" />
              Export
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <div>
              <p className="text-xs uppercase text-muted-foreground">Health</p>
              <p className="text-2xl font-bold tabular-nums">{pct(loop.health.score)}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {loop.health.basis || "nothing has measured this loop"}
              </p>
            </div>
            <div>
              <p className="text-xs uppercase text-muted-foreground">Last event</p>
              <p className="text-sm">{formatTime(loop.last_event)}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-muted-foreground">Events (7d)</p>
              <p className="text-2xl font-bold tabular-nums">{loop.event_count_7d}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-muted-foreground">Events (total)</p>
              <p className="text-2xl font-bold tabular-nums">
                {(loop.event_count_total ?? loop.event_count_7d).toLocaleString("en-US")}
              </p>
            </div>
          </div>

          {loop.description && (
            <div className="border-t pt-4">
              <p className="text-xs uppercase text-muted-foreground">Description</p>
              <p className="mt-2 text-sm">{loop.description}</p>
            </div>
          )}

          {loop.event_source && (
            <div className="border-t pt-4">
              <p className="text-xs uppercase text-muted-foreground">Read from</p>
              <p className="mt-2 text-sm text-muted-foreground">{loop.event_source}</p>
            </div>
          )}

          {recommendations.length > 0 && (
            <div className="border-t pt-4">
              <p className="text-xs uppercase text-muted-foreground">Notes</p>
              <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
                {recommendations.map((r) => (
                  <li key={r}>• {r}</li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      {error && (
        <Card className="border-destructive/40 bg-destructive/10">
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      <Tabs defaultValue="trend" className="w-full">
        <TabsList className="w-full">
          <TabsTrigger value="trend" className="flex-1">
            Activity
          </TabsTrigger>
          <TabsTrigger value="events" className="flex-1">
            Events ({events?.length ?? 0})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="trend">
          <Card>
            <CardContent className="pt-6">
              {loading ? (
                <div className="flex justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <div className="space-y-6">
                  {scored.length > 0 && (
                    <div>
                      {/* These three are computed over the trend window only,
                          while the health figure above is all-time. Two numbers
                          of the same unit over different periods must each name
                          their period, or the smaller window reads as a
                          contradiction of the larger one. */}
                      <p className="mb-2 text-xs uppercase text-muted-foreground">
                        Daily success rate over the last {trend?.points.length ?? 0} days
                        {" · "}
                        {scored.length} {scored.length === 1 ? "day" : "days"} with recorded outcomes
                      </p>
                      <div className="grid grid-cols-3 gap-4">
                        <div className="rounded border p-4">
                          <p className="text-xs uppercase text-muted-foreground">Min</p>
                          <p className="text-lg font-semibold tabular-nums">{pct(trend?.min_score)}</p>
                        </div>
                        <div className="rounded border p-4">
                          <p className="text-xs uppercase text-muted-foreground">Avg</p>
                          <p className="text-lg font-semibold tabular-nums">{pct(trend?.avg_score)}</p>
                        </div>
                        <div className="rounded border p-4">
                          <p className="text-xs uppercase text-muted-foreground">Max</p>
                          <p className="text-lg font-semibold tabular-nums">{pct(trend?.max_score)}</p>
                        </div>
                      </div>
                    </div>
                  )}
                  <TrendChart points={trend?.points ?? []} />
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="events">
          <Card>
            <CardContent className="pt-6">
              {loading ? (
                <div className="flex justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : events && events.length > 0 ? (
                <div className="max-h-96 space-y-2 overflow-y-auto">
                  {events.map((event, i) => (
                    <div
                      key={`${event.timestamp}-${i}`}
                      className="rounded border bg-muted/40 p-3 text-sm"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <p className="font-medium">{event.event_type}</p>
                          <p className="text-xs text-muted-foreground">
                            {formatTime(event.timestamp)}
                          </p>
                        </div>
                        {event.outcome && (
                          <Badge
                            variant="outline"
                            className={
                              event.outcome === "success"
                                ? "border-emerald-300 bg-emerald-100 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-300"
                                : "border-red-300 bg-red-100 text-red-800 dark:border-red-500/40 dark:bg-red-500/15 dark:text-red-300"
                            }
                          >
                            {event.outcome}
                          </Badge>
                        )}
                      </div>
                      {event.signal && (
                        <p className="mt-2 font-mono text-xs text-muted-foreground">{event.signal}</p>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="py-8 text-center text-sm text-muted-foreground">
                  No dated events recorded for this loop.
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};
