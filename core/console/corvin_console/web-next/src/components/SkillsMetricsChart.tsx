/**
 * SkillsMetricsChart — Advanced dashboard visualization for skill metrics (Phase 6)
 *
 * Displays:
 * - Score trend (line chart over epochs)
 * - Feedback breakdown (pie chart: outcome, task_shape)
 * - Anomaly alerts (visual badges)
 * - Performance metrics (error rate, latency)
 */

import React, { useMemo } from "react";
import {
  LineChart, Line, PieChart, Pie, Cell, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { AlertTriangle, TrendingUp, TrendingDown } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface MetricsData {
  skill_id: string;
  version: string;
  metrics: {
    total_runs: number;
    total_errors: number;
    score_history: Array<{ epoch: number; score: number; timestamp: string }>;
    score_trend: number;
    feedback_breakdown: {
      by_outcome: Record<string, number>;
      by_task_shape: Record<string, number>;
      by_decision: Record<string, number>;
    };
    anomalies: string[];
  };
  recommendations: string[];
}

interface SkillsMetricsChartProps {
  data: MetricsData;
}

const COLORS_FEEDBACK = ["#10b981", "#ef4444", "#f59e0b", "#8b5cf6"];

export const SkillsMetricsChart: React.FC<SkillsMetricsChartProps> = ({ data }) => {
  const { metrics } = data;

  // Prepare pie data (outcomes) — fix Issue 4: null check
  const pieData = useMemo(() => {
    const feedback = metrics.feedback_breakdown?.by_outcome || {};
    if (!feedback || typeof feedback !== 'object') return [];
    return Object.entries(feedback).map(([name, value]) => ({
      name,
      value,
    }));
  }, [metrics]);

  // Anomaly severity badge
  const getAnomalySeverity = (anomaly: string): "error" | "warning" => {
    if (anomaly.includes("error rate") || anomaly.includes("Score below")) return "error";
    return "warning";
  };

  // Trend indicator
  const trendIcon = metrics.score_trend > 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />;
  const trendColor = metrics.score_trend > 0 ? "text-green-600" : "text-red-600";

  return (
    <div className="space-y-4">
      {/* Score Trend Chart */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <span>Score Progression</span>
            <span className={`text-sm font-bold ${trendColor}`}>
              {trendIcon}
              {Math.abs(metrics.score_trend * 100).toFixed(1)}%
            </span>
          </CardTitle>
          <CardDescription>Learning curve over epochs</CardDescription>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={metrics.score_history}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="epoch"
                label={{ value: "Epoch", position: "insideBottomRight", offset: -5 }}
              />
              <YAxis
                domain={[0, 1]}
                label={{ value: "Score", angle: -90, position: "insideLeft" }}
              />
              <Tooltip formatter={(value) => (typeof value === 'number' ? value.toFixed(3) : value)} />
              <Legend />
              <Line
                type="monotone"
                dataKey="score"
                stroke="#3b82f6"
                dot={{ fill: "#3b82f6", r: 4 }}
                activeDot={{ r: 6 }}
                name="Score"
              />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* Feedback Breakdown */}
      <Card>
        <CardHeader>
          <CardTitle>Feedback Breakdown</CardTitle>
          <CardDescription>Distribution by outcome (success/failure)</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex justify-center">
            <ResponsiveContainer width="100%" height={250}>
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={({ name, value, percent }) =>
                    `${name}: ${value} (${((percent ?? 0) * 100).toFixed(0)}%)`
                  }
                  outerRadius={80}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {pieData.map((_, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS_FEEDBACK[index % COLORS_FEEDBACK.length]} />
                  ))}
                </Pie>
                <Tooltip formatter={(value) => `${value} events`} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {/* Anomalies & Performance */}
      <Card className={metrics.anomalies.length > 0 ? "border-orange-200 bg-orange-50" : ""}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <AlertTriangle size={20} className={metrics.anomalies.length > 0 ? "text-orange-600" : "text-green-600"} />
            {metrics.anomalies.length > 0 ? "Anomalies Detected" : "All Systems Healthy"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {metrics.anomalies.length > 0 ? (
            metrics.anomalies.map((anomaly, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <Badge variant="outline" className={getAnomalySeverity(anomaly) === "error" ? "bg-red-100 text-red-900" : "bg-yellow-100 text-yellow-900"}>
                  {getAnomalySeverity(anomaly)}
                </Badge>
                <span className="text-sm">{anomaly}</span>
              </div>
            ))
          ) : (
            <p className="text-sm text-gray-600">No anomalies detected. Skill is learning normally.</p>
          )}

          {/* Performance Metrics */}
          <div className="grid grid-cols-2 gap-2 pt-4 border-t">
            <div className="text-sm">
              <div className="text-gray-600">Total Runs (24h)</div>
              <div className="text-lg font-bold">{metrics.total_runs}</div>
            </div>
            <div className="text-sm">
              <div className="text-gray-600">Error Rate</div>
              <div className="text-lg font-bold">
                {metrics.total_runs > 0
                  ? (Math.min(100, (metrics.total_errors / Math.max(metrics.total_runs, 1)) * 100)).toFixed(1)
                  : "0"}
                %
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Recommendations */}
      <Card className="border-blue-200 bg-blue-50">
        <CardHeader>
          <CardTitle className="text-blue-900">Recommendations</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-1 text-sm text-blue-800">
            {data.recommendations.map((rec, idx) => (
              <li key={idx} className="flex items-start gap-2">
                <span>•</span>
                <span>{rec}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
};

export default SkillsMetricsChart;
