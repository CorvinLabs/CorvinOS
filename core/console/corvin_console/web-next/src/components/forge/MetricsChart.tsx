import React, { useMemo } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export interface MetricsDataPoint {
  timestamp: number; // Unix timestamp in seconds
  latency_p95_ms: number;
  error_rate: number; // 0-100
  confidence: number; // 0-1
}

interface MetricsChartProps {
  data: MetricsDataPoint[];
  loading?: boolean;
}

/**
 * MetricsChart: Displays real-time latency, error rate, and confidence trends
 * Data points are 1-minute buckets over the last hour.
 */
export const MetricsChart: React.FC<MetricsChartProps> = ({
  data,
  loading = false,
}) => {
  const chartData = useMemo(() => {
    return data.map((point) => ({
      ...point,
      time: new Date(point.timestamp * 1000).toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
      }),
      confidence_pct: Math.round(point.confidence * 100),
    }));
  }, [data]);

  if (loading) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-muted-foreground">
          Loading metrics...
        </CardContent>
      </Card>
    );
  }

  if (chartData.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-muted-foreground">
          No metrics data available
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Real-time Metrics (Last 60 minutes)</CardTitle>
      </CardHeader>

      <CardContent className="space-y-6">
        {/* Latency Chart */}
        <div>
          <h3 className="text-sm font-semibold mb-4">P95 Latency (ms)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="time"
                tick={{ fontSize: 12 }}
                interval={Math.floor(chartData.length / 6)}
              />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--background)',
                  border: '1px solid var(--border)',
                  borderRadius: '8px',
                }}
                formatter={(value: any) => value.toFixed(0)}
              />
              <ReferenceLine
                y={1500}
                stroke="#ef4444"
                strokeDasharray="5 5"
                label="Budget (1500ms)"
              />
              <Line
                type="monotone"
                dataKey="latency_p95_ms"
                stroke="#3b82f6"
                dot={false}
                strokeWidth={2}
                name="P95 Latency"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Error Rate Chart */}
        <div>
          <h3 className="text-sm font-semibold mb-4">Error Rate (%)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="time"
                tick={{ fontSize: 12 }}
                interval={Math.floor(chartData.length / 6)}
              />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--background)',
                  border: '1px solid var(--border)',
                  borderRadius: '8px',
                }}
                formatter={(value: any) => value.toFixed(2)}
              />
              <ReferenceLine
                y={5}
                stroke="#ef4444"
                strokeDasharray="5 5"
                label="SLO (5%)"
              />
              <Line
                type="monotone"
                dataKey="error_rate"
                stroke="#ef4444"
                dot={false}
                strokeWidth={2}
                name="Error Rate"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Confidence Score Chart */}
        <div>
          <h3 className="text-sm font-semibold mb-4">Confidence Score</h3>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="time"
                tick={{ fontSize: 12 }}
                interval={Math.floor(chartData.length / 6)}
              />
              <YAxis
                domain={[0, 100]}
                tick={{ fontSize: 12 }}
                label={{ value: '%', angle: -90, position: 'insideLeft' }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--background)',
                  border: '1px solid var(--border)',
                  borderRadius: '8px',
                }}
                formatter={(value: any) => value.toFixed(0)}
              />
              <ReferenceLine
                y={70}
                stroke="#10b981"
                strokeDasharray="5 5"
                label="Target (70%)"
              />
              <Line
                type="monotone"
                dataKey="confidence_pct"
                stroke="#10b981"
                dot={false}
                strokeWidth={2}
                name="Confidence"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
};

export default MetricsChart;
