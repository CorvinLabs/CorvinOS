/**
 * Layer Forge Analytics Dashboard (Phase 3B, ADR-2222 Phase 3)
 *
 * Four-chart operator dashboard:
 * 1. Decisions over time (stacked bar: accepted/flagged/rejected per week)
 * 2. Confidence trends (line: 7-day rolling avg)
 * 3. Review flags heatmap (scope_creep, security_gap, etc. count per week)
 * 4. Learning convergence (confidence delta over time)
 *
 * Follows dataviz skill: categorical colors for series, small multiples for
 * multiple measures, fail-closed on no data (no sample data shipped).
 */
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
  ScatterChart,
  Scatter,
} from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { AlertCircle, Download } from "lucide-react";
import { api, ApiError } from "@/lib/api/client";

const BASE = "/api/layer-forge";

interface AnalyticsData {
  decisions_accepted: number;
  decisions_flagged: number;
  decisions_rejected: number;
  mean_confidence: number;
  flags_distribution: Record<string, number>;
  decisions_by_week: Record<string, { accepted: number; flagged: number; rejected: number }>;
  flags_by_week: Record<string, Record<string, number>>;
  confidence_by_week: Record<string, number>;
  convergence: Array<{ timestamp: string; delta: number; entry_id: string; gate_id: string }>;
  window: {
    since: string;
    until: string;
    days: number;
  };
}

// Palette colors (from dataviz skill, light mode)
const COLOR_ACCEPTED = "#2a78d6"; // blue (series 1)
const COLOR_FLAGGED = "#eb6834"; // orange (series 2)
const COLOR_REJECTED = "#e34948"; // red (series 8)
const COLOR_TREND = "#1baf7a"; // aqua (series 3)

// Review flag type order (for consistent heatmap)
const FLAG_TYPES = [
  "scope_creep",
  "security_gap",
  "untested_complexity",
  "compliance_risk",
  "dependency_debt",
  "host_asymmetry",
  "unknown_risk",
];

const FLAG_LABELS: Record<string, string> = {
  scope_creep: "Scope Creep",
  security_gap: "Security Gap",
  untested_complexity: "Untested Complexity",
  compliance_risk: "Compliance Risk",
  dependency_debt: "Dependency Debt",
  host_asymmetry: "Host Asymmetry",
  unknown_risk: "Unknown Risk",
};

// Time range presets
type TimeRange = "7d" | "30d" | "90d" | "custom";

export function LayerForgeAnalyticsPage() {
  const [timeRange, setTimeRange] = useState<TimeRange>("30d");
  const [customSince, setCustomSince] = useState<string>("");
  const [customUntil, setCustomUntil] = useState<string>("");

  const queryParams = useMemo(() => {
    const now = new Date();
    let since = "", until = "";

    switch (timeRange) {
      case "7d":
        since = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString().split("T")[0];
        until = now.toISOString().split("T")[0];
        break;
      case "30d":
        since = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString().split("T")[0];
        until = now.toISOString().split("T")[0];
        break;
      case "90d":
        since = new Date(now.getTime() - 90 * 24 * 60 * 60 * 1000).toISOString().split("T")[0];
        until = now.toISOString().split("T")[0];
        break;
      case "custom":
        since = customSince;
        until = customUntil;
        break;
    }

    return new URLSearchParams(
      Object.entries({ since, until }).filter(([, v]) => v)
    ).toString();
  }, [timeRange, customSince, customUntil]);

  const { data, isLoading, error } = useQuery<AnalyticsData>({
    queryKey: ["layer-forge-analytics", queryParams],
    queryFn: async () => {
      const url = new URL(
        `${window.location.origin}${BASE}/analytics?${queryParams}`
      );
      const response = await fetch(url.toString());
      if (!response.ok) throw new Error("Failed to fetch analytics");
      return response.json();
    },
  });

  const decisionChartData = useMemo(() => {
    if (!data) return [];
    return Object.entries(data.decisions_by_week).map(([week, counts]) => ({
      week,
      accepted: counts.accepted,
      flagged: counts.flagged,
      rejected: counts.rejected,
    }));
  }, [data]);

  const confidenceChartData = useMemo(() => {
    if (!data) return [];
    return Object.entries(data.confidence_by_week).map(([week, avg]) => ({
      week,
      confidence: parseFloat(avg.toFixed(3)),
    }));
  }, [data]);

  const flagsChartData = useMemo(() => {
    if (!data) return [];
    return Object.entries(data.flags_by_week).map(([week, flags]) => ({
      week,
      ...flags,
    }));
  }, [data]);

  const convergenceChartData = useMemo(() => {
    if (!data) return [];
    // Sort by timestamp ascending for time-series visualization
    return [...data.convergence].reverse().slice(0, 50); // Last 50 events
  }, [data]);

  const handleExportCSV = () => {
    if (!data) return;

    const rows = [
      ["Layer Forge Analytics Export"],
      ["Window", `${data.window.since} to ${data.window.until}`, `(${data.window.days} days)`],
      [],
      ["Summary Metrics"],
      ["Accepted Decisions", data.decisions_accepted],
      ["Flagged Decisions", data.decisions_flagged],
      ["Rejected Decisions", data.decisions_rejected],
      ["Mean Confidence", data.mean_confidence],
      [],
      ["Decisions by Week"],
      ["Week", "Accepted", "Flagged", "Rejected"],
      ...Object.entries(data.decisions_by_week).map(([week, counts]) => [
        week,
        counts.accepted,
        counts.flagged,
        counts.rejected,
      ]),
      [],
      ["Flags Distribution"],
      ["Flag Type", "Count"],
      ...Object.entries(data.flags_distribution).map(([flag, count]) => [flag, count]),
    ];

    const csv = rows.map((row) => row.join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `layer-forge-analytics-${new Date().toISOString().split("T")[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Layer Forge Analytics</h1>
          <p className="text-sm text-muted-foreground">
            {data?.window && `${data.window.since} to ${data.window.until} (${data.window.days} days)`}
          </p>
        </div>
        <Button onClick={handleExportCSV} variant="outline" size="sm" disabled={!data}>
          <Download className="mr-2 h-4 w-4" />
          Export CSV
        </Button>
      </div>

      {/* Time Range Controls */}
      <div className="flex gap-2">
        {(["7d", "30d", "90d"] as TimeRange[]).map((range) => (
          <Button
            key={range}
            variant={timeRange === range ? "default" : "outline"}
            size="sm"
            onClick={() => setTimeRange(range)}
          >
            {range === "7d" ? "7 days" : range === "30d" ? "30 days" : "90 days"}
          </Button>
        ))}
        <Button
          variant={timeRange === "custom" ? "default" : "outline"}
          size="sm"
          onClick={() => setTimeRange("custom")}
        >
          Custom
        </Button>
      </div>

      {/* Custom Date Range (if selected) */}
      {timeRange === "custom" && (
        <div className="flex gap-2">
          <input
            type="date"
            value={customSince}
            onChange={(e) => setCustomSince(e.target.value)}
            className="px-2 py-1 border rounded text-sm"
            placeholder="Since (YYYY-MM-DD)"
          />
          <input
            type="date"
            value={customUntil}
            onChange={(e) => setCustomUntil(e.target.value)}
            className="px-2 py-1 border rounded text-sm"
            placeholder="Until (YYYY-MM-DD)"
          />
        </div>
      )}

      {/* Loading / Error States */}
      {isLoading && (
        <Card>
          <CardContent className="flex items-center justify-center h-40">
            <p className="text-muted-foreground">Loading analytics...</p>
          </CardContent>
        </Card>
      )}

      {error && (
        <Card className="border-destructive">
          <CardContent className="flex items-center gap-2 h-20 text-destructive">
            <AlertCircle className="h-5 w-5" />
            <span>Failed to load analytics</span>
          </CardContent>
        </Card>
      )}

      {!data && !isLoading && !error && (
        <Card>
          <CardContent className="flex items-center justify-center h-40">
            <p className="text-muted-foreground">No data available</p>
          </CardContent>
        </Card>
      )}

      {data && (
        <>
          {/* Summary Metrics */}
          <div className="grid grid-cols-4 gap-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Accepted</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-blue-600">{data.decisions_accepted}</div>
                <p className="text-xs text-muted-foreground mt-1">definitions deployed</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Flagged</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-orange-600">{data.decisions_flagged}</div>
                <p className="text-xs text-muted-foreground mt-1">under review</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Rejected</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-red-600">{data.decisions_rejected}</div>
                <p className="text-xs text-muted-foreground mt-1">superseded/failed</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Avg Confidence</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-green-600">
                  {(data.mean_confidence * 100).toFixed(0)}%
                </div>
                <p className="text-xs text-muted-foreground mt-1">rolling 7-day avg</p>
              </CardContent>
            </Card>
          </div>

          {/* Chart 1: Decisions Over Time */}
          <Card>
            <CardHeader>
              <CardTitle>Decisions Over Time</CardTitle>
              <CardDescription>Stacked bar chart: accepted, flagged, rejected per week</CardDescription>
            </CardHeader>
            <CardContent>
              {decisionChartData.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">No data for selected period</div>
              ) : (
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={decisionChartData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="week" />
                    <YAxis />
                    <Tooltip />
                    <Legend />
                    <Bar dataKey="accepted" stackId="a" fill={COLOR_ACCEPTED} name="Accepted" />
                    <Bar dataKey="flagged" stackId="a" fill={COLOR_FLAGGED} name="Flagged" />
                    <Bar dataKey="rejected" stackId="a" fill={COLOR_REJECTED} name="Rejected" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          {/* Chart 2: Confidence Trends */}
          <Card>
            <CardHeader>
              <CardTitle>Confidence Trends</CardTitle>
              <CardDescription>7-day rolling average confidence score</CardDescription>
            </CardHeader>
            <CardContent>
              {confidenceChartData.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">No data for selected period</div>
              ) : (
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={confidenceChartData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="week" />
                    <YAxis domain={[0, 1]} label={{ value: "Confidence", angle: -90, position: "insideLeft" }} />
                    <Tooltip formatter={(value) => (typeof value === "number" ? value.toFixed(3) : value)} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="confidence"
                      stroke={COLOR_TREND}
                      name="Mean Confidence"
                      dot={{ r: 4 }}
                      strokeWidth={2}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          {/* Chart 3: Review Flags Heatmap */}
          <Card>
            <CardHeader>
              <CardTitle>Review Flags Distribution</CardTitle>
              <CardDescription>Count of each flag type per week</CardDescription>
            </CardHeader>
            <CardContent>
              {flagsChartData.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">No flagged decisions</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr>
                        <th className="text-left py-2 px-2 font-medium">Week</th>
                        {FLAG_TYPES.map((flag) => (
                          <th key={flag} className="text-right py-2 px-2 font-medium">
                            {FLAG_LABELS[flag]}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {flagsChartData.map((row) => (
                        <tr key={row.week} className="border-t">
                          <td className="py-2 px-2 font-medium">{row.week}</td>
                          {FLAG_TYPES.map((flag) => (
                            <td key={`${row.week}-${flag}`} className="text-right py-2 px-2">
                              <Badge variant={row[flag] > 0 ? "secondary" : "outline"}>
                                {row[flag] || 0}
                              </Badge>
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Chart 4: Learning Convergence */}
          <Card>
            <CardHeader>
              <CardTitle>Learning Convergence</CardTitle>
              <CardDescription>Confidence deltas from quality gate outcomes (recent 50)</CardDescription>
            </CardHeader>
            <CardContent>
              {convergenceChartData.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">No learning events yet</div>
              ) : (
                <div className="space-y-2">
                  {convergenceChartData.slice(0, 20).map((event, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between p-2 border rounded bg-muted"
                    >
                      <div className="space-y-1">
                        <p className="text-sm font-medium">{event.entry_id}</p>
                        <p className="text-xs text-muted-foreground">{event.gate_id || "unknown"}</p>
                      </div>
                      <div className="text-right">
                        <p
                          className={`text-sm font-medium ${
                            event.delta > 0 ? "text-green-600" : event.delta < 0 ? "text-red-600" : ""
                          }`}
                        >
                          {event.delta > 0 ? "+" : ""}{event.delta.toFixed(2)}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {new Date(event.timestamp).toLocaleDateString()}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

export default LayerForgeAnalyticsPage;
