/**
 * Control Plane — Intent Router Dashboard (ADR-2028 Phase 9a)
 * Real-time request routing, confidence scores, success rates
 * Three dispatch paths: Skill Gen, Autonomy, Feedback
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  TrendingUp,
  AlertCircle,
  Copy,
  RefreshCw,
  CheckCircle,
  Clock,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/hooks/use-toast";

const API_BASE = "/v1/console/control-plane/intent-router";

interface DispatchPath {
  path_id: "skill_gen" | "autonomy" | "feedback";
  label: string;
  description: string;
  requests_total: number;
  requests_success: number;
  confidence_avg: number;
  latency_ms: number;
}

interface IntentRouterStats {
  timestamp: string;
  uptime_seconds: number;
  paths: DispatchPath[];
  recent_requests: Array<{
    request_id: string;
    intent: string;
    path_selected: string;
    confidence: number;
    success: boolean;
    latency_ms: number;
    timestamp: string;
  }>;
}

export default function ControlIntentRouterPage() {
  const queryClient = useQueryClient();
  const [autoRefresh, setAutoRefresh] = React.useState(true);

  // Fetch router stats
  const { data: stats, isLoading, error } = useQuery<IntentRouterStats>({
    queryKey: ["control-intent-router"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/stats`);
      if (!res.ok) throw new Error("Failed to load router stats");
      return res.json();
    },
    refetchInterval: autoRefresh ? 3000 : false,
    staleTime: 1000,
  });

  // Manual refresh
  const refreshMutation = useMutation({
    mutationFn: async () => {
      await queryClient.invalidateQueries({ queryKey: ["control-intent-router"] });
    },
  });

  // Reset statistics
  const resetMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/reset`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to reset stats");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-intent-router"] });
      toast({ title: "Statistics reset successfully" });
    },
    onError: () => {
      toast({ title: "Failed to reset stats", variant: "destructive" });
    },
  });

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast({ title: "Copied to clipboard" });
  };

  if (error) {
    return (
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        <header>
          <h1 className="font-serif text-3xl font-light tracking-tight">Intent Router</h1>
        </header>
        <Card className="border-destructive">
          <CardContent className="flex items-center gap-3 pt-6">
            <AlertCircle className="h-5 w-5 text-destructive" />
            <div>
              <p className="font-medium">Failed to load router stats</p>
              <p className="text-sm text-muted-foreground">
                {error instanceof Error ? error.message : "Unknown error"}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <header>
        <h1 className="font-serif text-3xl font-light tracking-tight">Intent Router Dashboard</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Real-time request routing, confidence scores, and success rates across dispatch paths
        </p>
      </header>

      {/* Control Bar */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
          aria-label="Manually refresh router stats"
        >
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setAutoRefresh(!autoRefresh)}
          aria-label={autoRefresh ? "Pause auto-refresh" : "Resume auto-refresh"}
        >
          {autoRefresh ? "Pause" : "Resume"} Auto-Refresh
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => resetMutation.mutate()}
          disabled={resetMutation.isPending}
          aria-label="Reset all router statistics"
        >
          Reset Stats
        </Button>
      </div>

      {/* Health Summary */}
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : stats ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle className="h-5 w-5 text-green-600" />
              Router Health
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <p className="text-sm text-muted-foreground">Uptime</p>
                <p className="text-xl font-semibold">
                  {Math.floor(stats.uptime_seconds / 3600)}h{" "}
                  {Math.floor((stats.uptime_seconds % 3600) / 60)}m
                </p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Total Requests</p>
                <p className="text-xl font-semibold">
                  {stats.paths.reduce((sum, p) => sum + p.requests_total, 0).toLocaleString()}
                </p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Overall Success Rate</p>
                <p className="text-xl font-semibold">
                  {(
                    (stats.paths.reduce((sum, p) => sum + p.requests_success, 0) /
                      (stats.paths.reduce((sum, p) => sum + p.requests_total, 0) || 1)) *
                    100
                  ).toFixed(1)}
                  %
                </p>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Last updated: {new Date(stats.timestamp).toUTCString()}
            </p>
          </CardContent>
        </Card>
      ) : null}

      {/* Dispatch Paths */}
      <div className="space-y-4">
        <h2 className="font-semibold">Dispatch Paths</h2>
        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-48 w-full" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {stats?.paths.map((path) => (
              <Card key={path.path_id}>
                <CardHeader className="pb-3">
                  <CardTitle className="text-lg">{path.label}</CardTitle>
                  <CardDescription>{path.description}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div>
                    <p className="text-sm text-muted-foreground">Requests</p>
                    <p className="text-2xl font-semibold">{path.requests_total.toLocaleString()}</p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Success Rate</p>
                    <div className="flex items-center gap-2">
                      <div className="flex-1">
                        <div className="h-2 w-full rounded-full bg-muted">
                          <div
                            className="h-2 rounded-full bg-green-600"
                            style={{
                              width: `${(path.requests_success / (path.requests_total || 1)) * 100}%`,
                            }}
                          />
                        </div>
                      </div>
                      <span className="text-sm font-medium">
                        {(
                          (path.requests_success / (path.requests_total || 1)) *
                          100
                        ).toFixed(0)}
                        %
                      </span>
                    </div>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Avg Confidence</p>
                    <p className="text-lg font-semibold">{path.confidence_avg.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Avg Latency</p>
                    <p className="text-lg font-semibold">{path.latency_ms.toFixed(0)}ms</p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Recent Requests */}
      <div className="space-y-4">
        <h2 className="font-semibold">Recent Requests</h2>
        {isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <Card>
            <CardContent className="pt-6">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="pb-2 text-left font-semibold">Intent</th>
                      <th className="pb-2 text-left font-semibold">Path</th>
                      <th className="pb-2 text-center font-semibold">Confidence</th>
                      <th className="pb-2 text-center font-semibold">Status</th>
                      <th className="pb-2 text-center font-semibold">Latency</th>
                      <th className="pb-2 text-left font-semibold">Time</th>
                      <th className="pb-2 text-center font-semibold">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats?.recent_requests.slice(0, 10).map((req) => (
                      <tr key={req.request_id} className="border-b hover:bg-muted/50">
                        <td className="py-3 font-mono text-xs">{req.intent.substring(0, 30)}</td>
                        <td className="py-3">
                          <Badge variant="outline">{req.path_selected}</Badge>
                        </td>
                        <td className="py-3 text-center">{req.confidence.toFixed(2)}</td>
                        <td className="py-3 text-center">
                          {req.success ? (
                            <CheckCircle className="mx-auto h-4 w-4 text-green-600" />
                          ) : (
                            <AlertCircle className="mx-auto h-4 w-4 text-destructive" />
                          )}
                        </td>
                        <td className="py-3 text-center text-muted-foreground">
                          {req.latency_ms}ms
                        </td>
                        <td className="py-3 text-xs text-muted-foreground">
                          {new Date(req.timestamp).toLocaleTimeString("en-US")}
                        </td>
                        <td className="py-3 text-center">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => copyToClipboard(req.request_id)}
                            aria-label="Copy request ID"
                          >
                            <Copy className="h-3 w-3" />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
