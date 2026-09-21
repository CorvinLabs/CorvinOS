import React from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Loader2, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDetail } from "@/hooks/use-learning-loops-detail";
import { formatTime, getStatusColor } from "@/utils/learning-loop-formatting";

interface LoopEntry {
  loop_id: string;
  plugin_id: string;
  skill_id?: string | null;
  status: "active" | "dormant" | "stale" | "degrading";
  health: { score: number; trend: "up" | "down" | "flat" };
  last_event?: string;
  event_count_7d: number;
  event_count_30d?: number;
  description?: string;
}

export const LearningLoopDetail: React.FC<{ loop: LoopEntry }> = ({ loop }) => {
  const { trend, events, loading } = useDetail(loop.loop_id);

  const exportJSON = () => {
    const data = { loop, trend, events };
    const json = JSON.stringify(data, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${loop.loop_id}-export.json`;
    a.click();
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex justify-between items-start">
            <div>
              <CardTitle className="flex items-center gap-2">
                {loop.plugin_id}
                <Badge variant="outline" className={getStatusColor(loop.status)}>
                  {loop.status}
                </Badge>
              </CardTitle>
              <p className="text-sm text-gray-600 mt-2">{loop.loop_id}</p>
            </div>
            <Button variant="outline" size="sm" onClick={exportJSON}>
              <Download className="h-4 w-4 mr-2" />
              Export
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <p className="text-xs text-gray-500 uppercase">Health</p>
              <p className="text-2xl font-bold">{(loop.health.score * 100).toFixed(0)}%</p>
            </div>
            <div>
              <p className="text-xs text-gray-500 uppercase">Last Event</p>
              <p className="text-sm">{formatTime(loop.last_event)}</p>
            </div>
            <div>
              <p className="text-xs text-gray-500 uppercase">Events (7d)</p>
              <p className="text-2xl font-bold">{loop.event_count_7d}</p>
            </div>
            <div>
              <p className="text-xs text-gray-500 uppercase">Events (30d)</p>
              <p className="text-2xl font-bold">{loop.event_count_30d || 0}</p>
            </div>
          </div>
          {loop.description && (
            <div className="border-t pt-4">
              <p className="text-xs text-gray-500 uppercase">Description</p>
              <p className="text-sm mt-2">{loop.description}</p>
            </div>
          )}
        </CardContent>
      </Card>

      <Tabs defaultValue="trend" className="w-full">
        <TabsList className="w-full">
          <TabsTrigger value="trend" className="flex-1">
            Trend
          </TabsTrigger>
          <TabsTrigger value="events" className="flex-1">
            Events ({events?.length || 0})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="trend">
          <Card>
            <CardContent className="pt-6">
              {loading ? (
                <div className="flex justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
                </div>
              ) : trend ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-3 gap-4">
                    <div className="border rounded p-4">
                      <p className="text-xs text-gray-500 uppercase">Min</p>
                      <p className="text-lg font-semibold">{(trend.min_score * 100).toFixed(0)}%</p>
                    </div>
                    <div className="border rounded p-4">
                      <p className="text-xs text-gray-500 uppercase">Avg</p>
                      <p className="text-lg font-semibold">{(trend.avg_score * 100).toFixed(0)}%</p>
                    </div>
                    <div className="border rounded p-4">
                      <p className="text-xs text-gray-500 uppercase">Max</p>
                      <p className="text-lg font-semibold">{(trend.max_score * 100).toFixed(0)}%</p>
                    </div>
                  </div>

                  <div className="border rounded p-4">
                    <p className="text-xs text-gray-500 uppercase mb-3">7-Day Health Trend</p>
                    <div className="flex gap-1 items-end h-24">
                      {trend.points.map((point, i) => {
                        const height = (point.health_score / (trend.max_score || 1)) * 100;
                        return (
                          <div
                            key={i}
                            className="flex-1 bg-blue-600 rounded-t"
                            style={{ height: `${Math.max(height, 5)}%` }}
                            title={`${point.date}: ${(point.health_score * 100).toFixed(0)}% (${point.event_count} events)`}
                          />
                        );
                      })}
                    </div>
                    <div className="flex justify-between text-xs text-gray-500 mt-2">
                      {trend.points.length > 0 && (
                        <>
                          <span>{trend.points[0].date}</span>
                          <span>{trend.points[trend.points.length - 1].date}</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="text-gray-500 text-center py-8">No trend data available</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="events">
          <Card>
            <CardContent className="pt-6">
              {loading ? (
                <div className="flex justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
                </div>
              ) : events && events.length > 0 ? (
                <div className="space-y-2 max-h-96 overflow-y-auto">
                  {events.map((event, i) => (
                    <div key={i} className="border rounded p-3 text-sm bg-gray-50">
                      <div className="flex justify-between items-start">
                        <div>
                          <p className="font-medium">{event.event_type}</p>
                          <p className="text-xs text-gray-600">{formatTime(event.timestamp)}</p>
                        </div>
                        {event.outcome && <Badge variant="outline">{event.outcome}</Badge>}
                      </div>
                      {event.signal && (
                        <p className="text-xs text-gray-600 mt-2">Signal: {event.signal}</p>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-gray-500 text-center py-8">No events available</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};
