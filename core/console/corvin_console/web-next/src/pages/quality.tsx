/**
 * Quality Gates Vibe Dashboard Panel (Phase 2.2)
 *
 * ADR-0688: Quality Gates System — comprehensive gate system for architectural
 * decisions, concepts, ADRs, and implementation plans.
 *
 * Panel Sections:
 * 1. Status (Last 24h) — table showing pass/fail/warn counts + pass%
 * 2. Trend (7 days) — line chart showing pass% trend over 7 days
 * 3. Recent Failures — list of recent gate failures with reason + timestamp
 * 4. Action buttons — Run All Gates, Settings, Help
 *
 * Data fetched from:
 * - GET /v1/console/quality/gates/status → status data
 * - GET /v1/console/quality/gates/history/<artifact_id> → historical data
 */

import * as React from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import {
  AlertTriangle,
  AlertCircle,
  CheckCircle2,
  Clock,
  Loader2,
  RefreshCw,
  Settings,
  HelpCircle,
  PlayCircle,
  TrendingUp,
  TrendingDown,
  ChevronDown,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, formatDate } from "@/lib/utils";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

// ── Types ──────────────────────────────────────────────────────────────────

interface GateStatus {
  gate_name: string;
  status: "pass" | "fail" | "warn";
  pass_count: number;
  fail_count: number;
  warn_count: number;
  pass_percentage: number;
  last_updated: string;
  artifact_type: "idea" | "concept" | "adr" | "implementation_plan";
}

interface GateFailure {
  failure_id: string;
  gate_name: string;
  artifact_id: string;
  artifact_type: string;
  failure_reason: string;
  severity: "error" | "warning";
  timestamp: string;
  attempted_fix?: string;
}

interface TrendDataPoint {
  date: string;
  pass_percentage: number;
  artifact_count: number;
}

interface GateStatusResponse {
  statuses: GateStatus[];
  summary: {
    total_gates: number;
    passing_gates: number;
    failing_gates: number;
    warning_gates: number;
    overall_pass_percentage: number;
  };
  timestamp: string;
}

interface GateHistoryResponse {
  artifact_id: string;
  trend: TrendDataPoint[];
  recent_failures: GateFailure[];
}

// ── API Functions ──────────────────────────────────────────────────────────

const fetchGateStatus = async (): Promise<GateStatusResponse> => {
  try {
    const response = await fetch("/v1/console/quality/gates/status", {
      method: "GET",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
    });

    if (!response.ok) {
      if (response.status === 404) {
        // Not deployed on this build — an empty result, never sample numbers.
        return {
          statuses: [],
          summary: {
            total_gates: 0,
            passing_gates: 0,
            failing_gates: 0,
            warning_gates: 0,
            overall_pass_percentage: 0,
          },
          timestamp: new Date().toISOString(),
        };
      }
      throw new Error(`API error: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error("Failed to fetch gate status:", error);
    throw error;
  }
};

const fetchGateHistory = async (artifactId: string = "_default"): Promise<GateHistoryResponse> => {
  try {
    const response = await fetch(`/v1/console/quality/gates/history/${artifactId}`, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
    });

    if (!response.ok) {
      if (response.status === 404) {
        // The endpoint is not deployed on this build. Return an EMPTY history,
        // never invented rows: the previous branch generated a seven-day trend
        // from Math.random() and three fabricated failures citing artifact ids
        // that do not exist, which is indistinguishable from real data on
        // screen and is exactly what must not ship.
        return { artifact_id: artifactId, trend: [], recent_failures: [] };
      }
      throw new Error(`API error: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error("Failed to fetch gate history:", error);
    throw error;
  }
};

const runAllGates = async (): Promise<{ ok: boolean; message: string }> => {
  try {
    const response = await fetch("/v1/console/quality/gates/run-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
    });

    if (!response.ok) {
      if (response.status === 404) {
        // The endpoint is not deployed on this build. Say so — reporting
        // "initiated" for a run that was never started is a false success the
        // operator would wait on.
        return {
          ok: false,
          message: "Quality gates are not available on this build.",
        };
      }
      throw new Error(`API error: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error("Failed to run gates:", error);
    throw error;
  }
};

// ── Status Badge Component ─────────────────────────────────────────────────

function StatusBadge({ status }: { status: "pass" | "fail" | "warn" }) {
  switch (status) {
    case "pass":
      return (
        <Badge className="bg-emerald-600 text-white hover:bg-emerald-700">
          <CheckCircle2 className="w-3 h-3 mr-1" /> Pass
        </Badge>
      );
    case "fail":
      return (
        <Badge className="bg-red-600 text-white hover:bg-red-700">
          <AlertCircle className="w-3 h-3 mr-1" /> Fail
        </Badge>
      );
    case "warn":
      return (
        <Badge className="bg-amber-600 text-white hover:bg-amber-700">
          <AlertTriangle className="w-3 h-3 mr-1" /> Warn
        </Badge>
      );
  }
}

// ── Status Table ───────────────────────────────────────────────────────────

function StatusTable({ statuses }: { statuses: GateStatus[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Gate Status (Last 24h)</CardTitle>
        <CardDescription>Current status of all quality gates</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="rounded-lg border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800">
              <tr>
                <th className="text-left px-4 py-3 font-semibold">Gate Name</th>
                <th className="text-center px-4 py-3 font-semibold">Status</th>
                <th className="text-center px-4 py-3 font-semibold">Pass</th>
                <th className="text-center px-4 py-3 font-semibold">Fail</th>
                <th className="text-center px-4 py-3 font-semibold">Warn</th>
                <th className="text-right px-4 py-3 font-semibold">Pass %</th>
              </tr>
            </thead>
            <tbody>
              {statuses.map((gate) => (
                <tr key={gate.gate_name} className="hover:bg-gray-50 dark:hover:bg-gray-900 border-b border-gray-200 dark:border-gray-800 last:border-b-0">
                  <td className="px-4 py-3 font-medium">{gate.gate_name}</td>
                  <td className="text-center px-4 py-3">
                    <StatusBadge status={gate.status} />
                  </td>
                  <td className="text-center px-4 py-3 text-emerald-600 dark:text-emerald-400">
                    {gate.pass_count}
                  </td>
                  <td className="text-center px-4 py-3 text-red-600 dark:text-red-400">
                    {gate.fail_count}
                  </td>
                  <td className="text-center px-4 py-3 text-amber-600 dark:text-amber-400">
                    {gate.warn_count}
                  </td>
                  <td className="text-right px-4 py-3 font-semibold">
                    {gate.pass_percentage.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Trend Chart ────────────────────────────────────────────────────────────

function TrendChart({ trend }: { trend: TrendDataPoint[] }) {
  const avgPassPercentage =
    trend.reduce((sum, d) => sum + d.pass_percentage, 0) / trend.length;
  const lastValue = trend[trend.length - 1]?.pass_percentage || 0;
  const firstValue = trend[0]?.pass_percentage || 0;
  const trend_direction = lastValue >= firstValue ? "up" : "down";

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-lg">Pass Rate Trend (7 days)</CardTitle>
            <CardDescription>
              Average: {avgPassPercentage.toFixed(1)}% — Trend:
              {trend_direction === "up" ? (
                <span className="text-emerald-600 dark:text-emerald-400 ml-2 inline-flex items-center gap-1">
                  <TrendingUp className="w-4 h-4" /> Up
                </span>
              ) : (
                <span className="text-red-600 dark:text-red-400 ml-2 inline-flex items-center gap-1">
                  <TrendingDown className="w-4 h-4" /> Down
                </span>
              )}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="w-full h-80">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trend} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-gray-200 dark:stroke-gray-700" />
              <XAxis
                dataKey="date"
                className="text-xs"
                tick={{ fill: "currentColor" }}
              />
              <YAxis
                domain={[70, 100]}
                className="text-xs"
                tick={{ fill: "currentColor" }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "var(--background)",
                  border: "1px solid var(--border)",
                  borderRadius: "8px",
                }}
                formatter={(value: any) => `${(value as number).toFixed(1)}%`}
              />
              <Legend wrapperStyle={{ paddingTop: "20px" }} />
              <Line
                type="monotone"
                dataKey="pass_percentage"
                stroke="#10b981"
                strokeWidth={2}
                dot={{ fill: "#10b981", r: 4 }}
                activeDot={{ r: 6 }}
                name="Pass %"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Recent Failures ────────────────────────────────────────────────────────

function RecentFailures({ failures }: { failures: GateFailure[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Recent Failures (Last 24h)</CardTitle>
        <CardDescription>
          {failures.length} issue{failures.length !== 1 ? "s" : ""} detected
        </CardDescription>
      </CardHeader>
      <CardContent>
        {failures.length === 0 ? (
          <div className="py-8 text-center">
            <CheckCircle2 className="w-12 h-12 mx-auto text-emerald-600 dark:text-emerald-400 mb-2 opacity-50" />
            <p className="text-gray-600 dark:text-gray-400">No failures in the last 24 hours</p>
          </div>
        ) : (
          <div className="space-y-3">
            {failures.map((failure) => (
              <div
                key={failure.failure_id}
                className="p-4 rounded-lg border border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700 transition-colors"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <Badge
                        variant={failure.severity === "error" ? "danger" : "warn"}
                        className="text-xs font-semibold"
                      >
                        {failure.severity === "error" ? (
                          <AlertCircle className="w-3 h-3 mr-1" />
                        ) : (
                          <AlertTriangle className="w-3 h-3 mr-1" />
                        )}
                        {failure.severity.toUpperCase()}
                      </Badge>
                      <span className="font-semibold text-sm">{failure.gate_name}</span>
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        ({failure.artifact_type}: {failure.artifact_id})
                      </span>
                    </div>
                    <p className="text-sm text-gray-700 dark:text-gray-300 mb-2">
                      {failure.failure_reason}
                    </p>
                    {failure.attempted_fix && (
                      <div className="text-xs bg-blue-50 dark:bg-blue-950 text-blue-900 dark:text-blue-100 p-2 rounded border border-blue-200 dark:border-blue-800">
                        <span className="font-semibold">Fix attempted:</span> {failure.attempted_fix}
                      </div>
                    )}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    {new Date(failure.timestamp).toLocaleTimeString("en-US", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Summary Stats ──────────────────────────────────────────────────────────

function SummaryStats({ summary }: { summary: GateStatusResponse["summary"] }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
      <Card>
        <CardContent className="pt-6">
          <div className="text-center">
            <div className="text-3xl font-bold text-blue-600 dark:text-blue-400">
              {summary.total_gates}
            </div>
            <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">Total Gates</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <div className="text-center">
            <div className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">
              {summary.passing_gates}
            </div>
            <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">Passing</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <div className="text-center">
            <div className="text-3xl font-bold text-amber-600 dark:text-amber-400">
              {summary.warning_gates}
            </div>
            <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">Warnings</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <div className="text-center">
            <div className="text-3xl font-bold text-red-600 dark:text-red-400">
              {summary.failing_gates}
            </div>
            <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">Failing</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <div className="text-center">
            <div className="text-3xl font-bold text-purple-600 dark:text-purple-400">
              {summary.overall_pass_percentage.toFixed(1)}%
            </div>
            <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">Overall Pass</p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────

export default function QualityGatesPanel() {
  const queryClient = useQueryClient();

  // Fetch gate status (auto-refresh every 60s)
  const statusQuery = useQuery({
    queryKey: ["quality", "gates", "status"],
    queryFn: fetchGateStatus,
    refetchInterval: 60000, // 60 seconds
    retry: 2,
  });

  // Fetch gate history
  const historyQuery = useQuery({
    queryKey: ["quality", "gates", "history"],
    queryFn: () => fetchGateHistory(),
    refetchInterval: 60000,
    retry: 2,
  });

  // Run all gates mutation
  const runAllMutation = useMutation({
    mutationFn: runAllGates,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["quality", "gates"] });
    },
  });

  const isLoading =
    statusQuery.isPending || historyQuery.isPending;
  const isError = statusQuery.isError || historyQuery.isError;

  if (isLoading) {
    return (
      <div className="space-y-6 p-6">
        <div className="space-y-2">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-64" />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-80 rounded-lg" />
        <Skeleton className="h-64 rounded-lg" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-6">
        <Card className="border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950">
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <AlertCircle className="w-6 h-6 text-red-600 dark:text-red-400" />
              <div>
                <p className="font-semibold text-red-900 dark:text-red-100">
                  Failed to load quality gates
                </p>
                <p className="text-sm text-red-800 dark:text-red-200 mt-1">
                  Check your connection and try again.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const statusData = statusQuery.data;
  const historyData = historyQuery.data;

  if (!statusData || !historyData) {
    return null;
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Quality Gates</h1>
          <p className="text-gray-600 dark:text-gray-400 mt-1">
            Monitor architectural decision quality across ADRs, Concepts, and Implementation Plans
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              queryClient.invalidateQueries({ queryKey: ["quality", "gates"] });
            }}
            className="gap-2"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={() => runAllMutation.mutate()}
            disabled={runAllMutation.isPending}
            className="gap-2 bg-blue-600 hover:bg-blue-700 text-white"
          >
            {runAllMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <PlayCircle className="w-4 h-4" />
            )}
            Run All Gates
          </Button>
          <Button variant="outline" size="sm" className="gap-2">
            <Settings className="w-4 h-4" />
            Settings
          </Button>
          <Button variant="outline" size="sm" className="gap-2">
            <HelpCircle className="w-4 h-4" />
          </Button>
        </div>
      </div>

      {/* Summary Stats */}
      <SummaryStats summary={statusData.summary} />

      {/* Status Table */}
      <StatusTable statuses={statusData.statuses} />

      {/* Trend Chart */}
      <TrendChart trend={historyData.trend} />

      {/* Recent Failures */}
      <RecentFailures failures={historyData.recent_failures} />

      {/* Last Updated */}
      <div className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1">
        <Clock className="w-3 h-3" />
        Last updated: {new Date(statusData.timestamp).toLocaleString()}
      </div>
    </div>
  );
}
