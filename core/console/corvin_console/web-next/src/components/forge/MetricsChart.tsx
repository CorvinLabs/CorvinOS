import React from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { MetricPoint, Variant, VariantStats } from '@/lib/api/autonomous-forge';
import { VARIANT_COLOR, VARIANT_LABEL, formatScore, outcomeSeries } from './autonomous-forge-encoding';

interface MetricsChartProps {
  points: MetricPoint[];
  stats: Record<Variant, VariantStats> | null;
}

const VARIANTS: Variant[] = ['live', 'candidate'];
const AXIS = { fontSize: 12, fill: 'hsl(var(--muted-foreground))' };

/**
 * Running mean rating per variant over its own ratings (one shared 0–1 axis).
 * Below two ratings a line draws nothing, so the form degrades to bars.
 */
export const MetricsChart: React.FC<MetricsChartProps> = ({ points, stats }) => {
  const rows = outcomeSeries(points);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Mean rating as ratings arrive</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground py-8 text-center">
            No ratings yet. A rating is recorded when a user answers after a turn that used the skill.
          </p>
        ) : rows.length < 2 && stats ? (
          <ResponsiveContainer width="100%" height={180}>
            <BarChart
              data={VARIANTS.map((v) => ({ variant: VARIANT_LABEL[v], key: v, mean: stats[v].outcome_mean ?? 0 }))}
              layout="vertical"
              margin={{ left: 16, right: 24 }}
            >
              <CartesianGrid horizontal={false} stroke="var(--viz-grid)" />
              <XAxis type="number" domain={[0, 1]} tick={AXIS} />
              <YAxis type="category" dataKey="variant" tick={AXIS} width={80} />
              <Tooltip formatter={(v: number) => formatScore(v)} />
              <Bar dataKey="mean" minPointSize={2} radius={[0, 4, 4, 0]} barSize={18}
                shape={(props: any) => (
                  <rect {...props} fill={VARIANT_COLOR[props.payload.key as Variant]} rx={4} />
                )}
              />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={rows} margin={{ left: 0, right: 24, top: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
              <XAxis dataKey="n" tick={AXIS} label={{ value: 'Rating #', position: 'insideBottomRight', offset: -4, ...AXIS }} />
              <YAxis domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tick={AXIS} width={36} />
              <Tooltip
                formatter={(v: number, name: string) => [formatScore(v), name]}
                labelFormatter={(n) => `Rating #${n}`}
              />
              <Legend
                formatter={(value: string) => <span style={{ color: 'hsl(var(--foreground))' }}>{value}</span>}
              />
              {VARIANTS.map((v) => (
                <Line
                  key={v}
                  type="monotone"
                  dataKey={v}
                  name={VARIANT_LABEL[v]}
                  stroke={VARIANT_COLOR[v]}
                  strokeWidth={2}
                  strokeDasharray={v === 'candidate' ? '6 3' : undefined}
                  dot={{ r: 4, strokeWidth: 2, stroke: 'hsl(var(--card))', fill: VARIANT_COLOR[v] }}
                  connectNulls
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
};

export default MetricsChart;
