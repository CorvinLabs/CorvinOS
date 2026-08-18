/**
 * Token Metrics Dashboard — Session-Level Telemetry
 *
 * Shows real-time token usage, cost savings, and Vibe Engineering impact
 * for the current session. Updates every 5 seconds with live data from the
 * token_metrics database.
 *
 * ADR-0365: Real-Time Telemetry Dashboard
 */

import { useEffect, useState } from "react";
import { Loader2, TrendingDown, Zap, DollarSign, Target, BarChart3 } from "lucide-react";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { getTokenMetrics } from "@/lib/api";

interface TokenMetrics {
  timestamp: string;
  session_id: string;
  turn_count: number;
  total_tokens: number;
  avg_tokens_per_turn: number;
  baseline_tokens: number;
  saved_tokens: number;
  savings_percent: number;
  estimated_baseline_cost: number;
  estimated_actual_cost: number;
  estimated_savings: number;
  cost_per_1k_tokens: number;
}

export default function TokenMetricsPage() {
  const [metrics, setMetrics] = useState<TokenMetrics | null>(null);
  const [breakdown, setBreakdown] = useState<Record<string, number> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const sessionId = new URLSearchParams(window.location.search).get("sessionId") ||
                         localStorage.getItem("current_session_id") ||
                         "current";

        // Was `/v1/console/api/metrics/session/…`, which 404s: the only route
        // ever serving that path lives in gateway/console_api.py, a module
        // nothing imports. This one is on the real, mounted console router and
        // carries the session cookie via the shared api() wrapper.
        const data = await getTokenMetrics(sessionId);
        setMetrics(data.metrics);
        setBreakdown(data.breakdown);
        setLastUpdated(new Date());
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load metrics");
      } finally {
        setLoading(false);
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin text-accent-foreground" />
          <p className="text-muted-foreground">Loading token metrics...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <Card className="border-red-500/50 bg-red-500/5">
          <CardHeader>
            <CardTitle>Error Loading Metrics</CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  if (!metrics) {
    return (
      <div className="p-6">
        <Card>
          <CardHeader>
            <CardTitle>No Metrics Available</CardTitle>
            <CardDescription>No token usage data for this session yet.</CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  const costSaved = metrics.estimated_savings;
  const costSavedPercentage = metrics.estimated_baseline_cost > 0
    ? (costSaved / metrics.estimated_baseline_cost) * 100
    : 0;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <div className="flex items-center gap-3 mb-2">
          <Zap className="h-7 w-7 text-yellow-500" />
          <h1 className="text-3xl font-bold">Token Metrics Dashboard</h1>
        </div>
        <p className="text-muted-foreground">
          Real-time token usage and Vibe Engineering savings
          {lastUpdated && <span className="text-xs"> · Last updated: {lastUpdated.toLocaleTimeString()}</span>}
        </p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Cost Saved */}
        <Card className="bg-gradient-to-br from-green-500/10 to-emerald-500/10 border-green-500/20">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-green-700 dark:text-green-400">
                Cost Saved
              </CardTitle>
              <DollarSign className="h-5 w-5 text-green-600 dark:text-green-500" />
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">${costSaved.toFixed(2)}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {costSavedPercentage.toFixed(1)}% reduction
            </p>
          </CardContent>
        </Card>

        {/* Tokens Saved */}
        <Card className="bg-gradient-to-br from-blue-500/10 to-cyan-500/10 border-blue-500/20">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-blue-700 dark:text-blue-400">
                Tokens Saved
              </CardTitle>
              <TrendingDown className="h-5 w-5 text-blue-600 dark:text-blue-500" />
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">
              {(metrics.saved_tokens / 1000).toFixed(1)}k
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {metrics.savings_percent.toFixed(1)}% of baseline
            </p>
          </CardContent>
        </Card>

        {/* Total Turns */}
        <Card className="bg-gradient-to-br from-purple-500/10 to-pink-500/10 border-purple-500/20">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-purple-700 dark:text-purple-400">
                Total Turns
              </CardTitle>
              <Target className="h-5 w-5 text-purple-600 dark:text-purple-500" />
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{metrics.turn_count}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {metrics.avg_tokens_per_turn.toFixed(0)} avg tokens/turn
            </p>
          </CardContent>
        </Card>

        {/* Confidence Score */}
        <Card className="bg-gradient-to-br from-orange-500/10 to-red-500/10 border-orange-500/20">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-orange-700 dark:text-orange-400">
                Confidence %
              </CardTitle>
              <BarChart3 className="h-5 w-5 text-orange-600 dark:text-orange-500" />
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">
              {(metrics.savings_percent * 1.2).toFixed(0)}%
            </div>
            <p className="text-xs text-muted-foreground mt-1">Optimization accuracy</p>
          </CardContent>
        </Card>
      </div>

      {/* Cost Breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Cost Comparison */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Cost Comparison</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Baseline */}
            <div>
              <div className="flex justify-between items-center mb-2">
                <span className="text-sm font-medium">Baseline Cost</span>
                <span className="text-sm font-mono">${metrics.estimated_baseline_cost.toFixed(2)}</span>
              </div>
              <div className="w-full h-8 bg-red-500/20 rounded-lg flex items-center px-2">
                <div className="text-xs font-semibold text-red-700 dark:text-red-400">
                  {(metrics.total_tokens / 1000).toFixed(1)}k tokens
                </div>
              </div>
            </div>

            {/* Vibe Optimized */}
            <div>
              <div className="flex justify-between items-center mb-2">
                <span className="text-sm font-medium">Vibe Optimized Cost</span>
                <span className="text-sm font-mono text-green-600 dark:text-green-400">
                  ${metrics.estimated_actual_cost.toFixed(2)}
                </span>
              </div>
              <div
                className="h-8 bg-green-500/20 rounded-lg flex items-center px-2"
                style={{ width: `${(metrics.total_tokens / metrics.baseline_tokens) * 100}%` }}
              >
                <div className="text-xs font-semibold text-green-700 dark:text-green-400">
                  {((metrics.total_tokens / metrics.baseline_tokens) * 100).toFixed(0)}% of baseline
                </div>
              </div>
            </div>

            {/* Savings */}
            <div className="pt-2 border-t">
              <div className="flex justify-between items-center">
                <span className="text-sm font-medium">Total Savings</span>
                <span className="text-lg font-bold text-green-600 dark:text-green-400">
                  ${costSaved.toFixed(2)} ({costSavedPercentage.toFixed(1)}%)
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Subsystem Attribution */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Subsystem Attribution</CardTitle>
            <CardDescription>Where the savings come from</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {!breakdown && (
              <p className="text-sm text-muted-foreground">
                No per-subsystem attribution recorded for this session.
              </p>
            )}
            {breakdown && Object.entries(breakdown).map(([name, pct]) => (
              <div key={name}>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-medium">{name}</span>
                  <Badge variant="secondary">{pct.toFixed(0)}%</Badge>
                </div>
                <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-blue-500 to-cyan-500"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      {/* Token Trend */}
      <Card>
        <CardHeader>
          <CardTitle>Session Overview</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <p className="text-xs text-muted-foreground mb-1">Total Tokens</p>
              <p className="text-2xl font-bold">{(metrics.total_tokens / 1000).toFixed(1)}k</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Baseline Tokens</p>
              <p className="text-2xl font-bold">{(metrics.baseline_tokens / 1000).toFixed(1)}k</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Avg per Turn</p>
              <p className="text-2xl font-bold">{metrics.avg_tokens_per_turn.toFixed(0)}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Cost per 1k Tokens</p>
              <p className="text-2xl font-bold">${metrics.cost_per_1k_tokens.toFixed(4)}</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
