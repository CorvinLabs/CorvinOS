/**
 * MetricsGraph Component
 *
 * Displays a simple SVG line graph of call rate over time (past 24h).
 * For Week 5 baseline, we expect 0 calls/min (Phase B: no old APIs in use).
 */

import React from 'react';
import { CurrentMetricsResponse } from '../../hooks/useDeprecatedApiMetrics';

interface MetricsGraphProps {
  metrics: CurrentMetricsResponse;
}

export function MetricsGraph({ metrics }: MetricsGraphProps) {
  // For Week 5, we generate 24 simulated data points for demonstration
  // (Phase 2 will wire this to real historical trend data from /v1/console/deprecated-apis/trend)
  const generateMockTrend = (): number[] => {
    // Expected pattern: 0 calls/min for Phase B
    return Array.from({ length: 24 }, (_, i) => {
      // Simulate a slightly increasing trend toward Phase C (should drop to 0 then)
      const baselineForWeek5 = 0.5;
      return baselineForWeek5 + (i % 3) * 0.1;
    });
  };

  const dataPoints = generateMockTrend();
  const maxValue = Math.max(...dataPoints, 10); // Min Y scale of 10
  const graphHeight = 200;
  const graphWidth = 600;
  const padding = { top: 20, right: 20, bottom: 30, left: 40 };

  const xScale = (graphWidth - padding.left - padding.right) / (dataPoints.length - 1);
  const yScale = (graphHeight - padding.top - padding.bottom) / maxValue;

  // Build path for line chart
  const pathData = dataPoints
    .map((val, i) => {
      const x = padding.left + i * xScale;
      const y = graphHeight - padding.bottom - val * yScale;
      return `${i === 0 ? 'M' : 'L'} ${x} ${y}`;
    })
    .join(' ');

  // Build fill area
  const areaData = `${pathData} L ${padding.left + (dataPoints.length - 1) * xScale} ${
    graphHeight - padding.bottom
  } L ${padding.left} ${graphHeight - padding.bottom} Z`;

  const gridStroke = 'hsl(var(--border))';
  const labelFill = 'hsl(var(--muted-foreground))';
  const accentColor = 'hsl(var(--accent))';

  return (
    <div className="flex flex-col gap-4">
      <svg width="100%" height={graphHeight} viewBox={`0 0 ${graphWidth} ${graphHeight}`} className="font-mono">
        {/* Grid lines */}
        {[0, 0.25, 0.5, 0.75, 1].map((pct, i) => {
          const y = graphHeight - padding.bottom - pct * (graphHeight - padding.top - padding.bottom);
          return (
            <g key={`hgrid-${i}`}>
              <line x1={padding.left} y1={y} x2={graphWidth - padding.right} y2={y} stroke={gridStroke} strokeWidth="1" />
              <text x={padding.left - 5} y={y + 4} fontSize="11" fill={labelFill} textAnchor="end">
                {(pct * maxValue).toFixed(0)}
              </text>
            </g>
          );
        })}

        {/* Vertical axis */}
        <line x1={padding.left} y1={padding.top} x2={padding.left} y2={graphHeight - padding.bottom} stroke={gridStroke} strokeWidth="2" />

        {/* Horizontal axis */}
        <line x1={padding.left} y1={graphHeight - padding.bottom} x2={graphWidth - padding.right} y2={graphHeight - padding.bottom} stroke={gridStroke} strokeWidth="2" />

        {/* Area fill */}
        <path d={areaData} fill={accentColor} fillOpacity="0.1" stroke="none" />

        {/* Line chart */}
        <path d={pathData} stroke={accentColor} strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />

        {/* Data points */}
        {dataPoints.map((val, i) => {
          const x = padding.left + i * xScale;
          const y = graphHeight - padding.bottom - val * yScale;
          return <circle key={`point-${i}`} cx={x} cy={y} r="3" fill={accentColor} opacity="0.6" />;
        })}

        {/* X-axis labels (every 6 hours) */}
        {[0, 6, 12, 18, 23].map((i) => {
          const x = padding.left + i * xScale;
          const hour = (i * 1) % 24;
          return (
            <text key={`xlabel-${i}`} x={x} y={graphHeight - padding.bottom + 20} fontSize="11" fill={labelFill} textAnchor="middle">
              {hour}h
            </text>
          );
        })}

        {/* Y-axis label */}
        <text x="10" y="15" fontSize="11" fill={labelFill}>
          calls/min
        </text>
      </svg>

      {/* Legend */}
      <div className="flex gap-6 text-xs text-muted-foreground">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-accent"></div>
          <span>Call Rate</span>
        </div>
        <div className="text-muted-foreground">SLO Target: &lt;10/min (Phase B)</div>
      </div>
    </div>
  );
}
