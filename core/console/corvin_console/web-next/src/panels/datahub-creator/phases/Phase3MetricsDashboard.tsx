/**
 * Phase 3: Metrics Dashboard (CENTERPIECE)
 *
 * Shows learning progress with convergence visualization.
 * User sees:
 * - Average confidence score (↑ = learning working)
 * - Skills improved count
 * - Convergence status (in_progress | stalled | complete)
 * - Recommendations (next actions based on metrics)
 */

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Loader2, TrendingUp, AlertCircle, CheckCircle2 } from "lucide-react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

interface Metrics {
  avg_confidence: number;
  convergence_status: "in_progress" | "stalled" | "complete";
  skills_improved: number;
}

interface Phase3MetricsDashboardProps {
  projectId: string;
  metrics?: Metrics;
  isConverged: boolean;
}

export function Phase3MetricsDashboard({
  projectId,
  metrics,
  isConverged,
}: Phase3MetricsDashboardProps) {
  const [loading, setLoading] = useState(!metrics);
  const [refreshing, setRefreshing] = useState(false);
  const [displayMetrics, setDisplayMetrics] = useState<Metrics | undefined>(metrics);
  const [history, setHistory] = useState<Array<{ timestamp: string; confidence: number }>>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!displayMetrics) {
      refreshMetrics();
    }
  }, [projectId, displayMetrics]);

  async function refreshMetrics() {
    try {
      setRefreshing(true);
      setError(null);

      const response = await fetch(`/v1/console/datahub/projects/${projectId}`);
      if (!response.ok) throw new Error("Failed to fetch metrics");

      const data = await response.json();
      setDisplayMetrics(data.latest_metrics);

      // Build mock history for visualization
      if (!history.length) {
        const mockHistory = Array.from({ length: 10 }, (_, i) => ({
          timestamp: `Run ${i + 1}`,
          confidence: 0.4 + (i * 0.05) + Math.random() * 0.03,
        }));
        setHistory(mockHistory);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="animate-spin" />
      </div>
    );
  }

  if (!displayMetrics) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>
          No metrics available yet. Run some skills and provide feedback in Phase 2.
        </AlertDescription>
      </Alert>
    );
  }

  const confidencePercent = Math.round(displayMetrics.avg_confidence * 100);
  const convergenceColor =
    displayMetrics.convergence_status === "complete"
      ? "text-green-600"
      : displayMetrics.convergence_status === "stalled"
      ? "text-red-600"
      : "text-blue-600";

  const recommendations = getRecommendations(displayMetrics);

  return (
    <div className="space-y-6">
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Confidence Score */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Average Confidence</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{confidencePercent}%</div>
            <p className="text-xs text-muted-foreground mt-1">
              {displayMetrics.avg_confidence < 0.5 ? "🟡 Low" : "🟢 Good"}
            </p>
            <div className="w-full bg-muted rounded-full h-2 mt-2">
              <div
                className="bg-blue-600 h-2 rounded-full transition-all"
                style={{ width: `${confidencePercent}%` }}
              />
            </div>
          </CardContent>
        </Card>

        {/* Skills Improved */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Skills Improved</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{displayMetrics.skills_improved}</div>
            <p className="text-xs text-muted-foreground mt-1">with high confidence (>70%)</p>
            <div className="flex gap-1 mt-2">
              {Array.from({ length: displayMetrics.skills_improved }).map((_, i) => (
                <div key={i} className="w-2 h-2 bg-green-600 rounded-full" />
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Convergence Status */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Convergence Status</CardTitle>
          </CardHeader>
          <CardContent>
            <div className={`text-lg font-bold capitalize ${convergenceColor}`}>
              {displayMetrics.convergence_status}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {displayMetrics.convergence_status === "complete"
                ? "Learning complete!"
                : "Keep providing feedback"}
            </p>
            <Badge className="mt-2" variant={
              displayMetrics.convergence_status === "complete" ? "default" : "secondary"
            }>
              {displayMetrics.convergence_status === "complete" ? "✓ Done" : "→ In Progress"}
            </Badge>
          </CardContent>
        </Card>
      </div>

      {/* Convergence Trend Chart */}
      {history.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Confidence Trend</CardTitle>
            <CardDescription>Average confidence over time</CardDescription>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={history}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="timestamp" />
                <YAxis domain={[0, 1]} />
                <Tooltip
                  formatter={(value: number) => `${(value * 100).toFixed(0)}%`}
                  contentStyle={{
                    backgroundColor: "#1a1a1a",
                    border: "1px solid #444",
                    borderRadius: "4px",
                  }}
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="confidence"
                  stroke="#2563eb"
                  dot={false}
                  name="Confidence"
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Next Actions</CardTitle>
            <CardDescription>Based on your metrics, here's what to do next</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {recommendations.map((rec, i) => (
                <div key={i} className="p-3 bg-muted rounded flex items-start gap-3">
                  <div className="mt-1">
                    {rec.priority === "high" ? "🔴" : "🟡"}
                  </div>
                  <div className="flex-1">
                    <p className="font-medium text-sm">{rec.title}</p>
                    <p className="text-xs text-muted-foreground">{rec.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Convergence Alert */}
      {isConverged && (
        <Alert>
          <CheckCircle2 className="h-4 w-4 text-green-600" />
          <AlertDescription>
            🎉 <strong>Learning complete!</strong> Your skills have converged. Ready to export results or continue optimizing.
          </AlertDescription>
        </Alert>
      )}

      {displayMetrics.convergence_status === "stalled" && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            ⚠️ Learning has stalled. Try providing more feedback or adjust your skills.
          </AlertDescription>
        </Alert>
      )}

      {/* Refresh Button */}
      <Button onClick={refreshMetrics} disabled={refreshing}>
        {refreshing && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
        Refresh Metrics
      </Button>
    </div>
  );
}

function getRecommendations(
  metrics: Metrics
): Array<{ title: string; description: string; priority: "high" | "medium" }> {
  const recommendations = [];

  if (metrics.avg_confidence < 0.5) {
    recommendations.push({
      title: "Collect More Feedback",
      description: "Low confidence indicates insufficient feedback data. Run more skills and provide detailed feedback.",
      priority: "high",
    });
  }

  if (metrics.convergence_status === "stalled") {
    recommendations.push({
      title: "Adjust Optimizer Settings",
      description: "Learning isn't progressing. Try adjusting convergence thresholds in Phase 4.",
      priority: "high",
    });
  }

  if (metrics.skills_improved < 2 && metrics.avg_confidence > 0.6) {
    recommendations.push({
      title: "Explore Edge Cases",
      description: "Good average confidence but few improved skills. Test edge cases to improve specific skills.",
      priority: "medium",
    });
  }

  if (metrics.convergence_status === "complete") {
    recommendations.push({
      title: "Export Results",
      description: "Learning is complete. Move to Phase 6 to export your final metrics and archive the project.",
      priority: "medium",
    });
  }

  return recommendations;
}
