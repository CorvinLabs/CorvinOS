/**
 * Hover Details — 24h Convergence Graph for Loop
 *
 * Shows real-time time-series of a single loop's convergence.
 * Displays: score trend over 24h + convergence rate + drift.
 */

import React, { useEffect, useState } from 'react';
import { X, TrendingUp } from 'lucide-react';

interface HistoricalPoint {
  timestamp: string;
  unix_time: number;
  score: number;
  convergence_rate: number;
  drift: number;
}

interface HoverDetailsProps {
  loopName: string;
  loopKey: string;
  onClose: () => void;
}

export function HoverDetails({ loopName, loopKey, onClose }: HoverDetailsProps) {
  const [data, setData] = useState<HistoricalPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadHistoricalData = async () => {
      try {
        setLoading(true);
        // Fetch 24h historical data from API
        const response = await fetch(
          `/v1/console/vibe/maturity/historical?loop=${loopKey}&window=today`,
          { headers: { 'Content-Type': 'application/json' } }
        );

        if (!response.ok) {
          throw new Error(`API error ${response.status}`);
        }

        const result = await response.json();
        setData(result.points || []);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load data');
        setData([]);
      } finally {
        setLoading(false);
      }
    };

    loadHistoricalData();
  }, [loopKey]);

  // Calculate stats
  const currentScore = data.length > 0 ? data[data.length - 1].score : 0;
  const startScore = data.length > 0 ? data[0].score : 0;
  const trend = currentScore - startScore;
  const avgConvergence = data.length > 0
    ? (data.reduce((sum, p) => sum + p.convergence_rate, 0) / data.length)
    : 0;
  const currentDrift = data.length > 0 ? data[data.length - 1].drift : 0;

  return (
    <div className="fixed inset-0 bg-background/70 backdrop-blur-sm flex items-center justify-center z-50">
      <div className="bg-card border border-border rounded-lg p-6 max-w-2xl w-full mx-4 max-h-[80vh] overflow-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-semibold text-foreground">{loopName} — 24h Analysis</h3>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground transition"
          >
            <X size={20} />
          </button>
        </div>

        {/* Stats Row */}
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-muted rounded p-3">
            <div className="text-xs text-muted-foreground uppercase mb-1">Current Score</div>
            <div className="text-xl font-bold text-accent">{currentScore.toFixed(1)}</div>
          </div>
          <div className="bg-muted rounded p-3">
            <div className="text-xs text-muted-foreground uppercase mb-1">24h Trend</div>
            <div className={`text-xl font-bold ${trend >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive'}`}>
              {trend >= 0 ? '+' : ''}{trend.toFixed(2)}
            </div>
          </div>
          <div className="bg-muted rounded p-3">
            <div className="text-xs text-muted-foreground uppercase mb-1">Avg Convergence</div>
            <div className="text-xl font-bold text-accent">{avgConvergence.toFixed(3)}</div>
          </div>
          <div className="bg-muted rounded p-3">
            <div className="text-xs text-muted-foreground uppercase mb-1">Current Drift</div>
            <div className="text-xl font-bold text-amber-600 dark:text-amber-400">{currentDrift.toFixed(3)}</div>
          </div>
        </div>

        {/* Time Series Chart (ASCII / SVG fallback) */}
        {loading ? (
          <div className="h-48 flex items-center justify-center text-muted-foreground">
            <span>Loading historical data...</span>
          </div>
        ) : error ? (
          <div className="h-48 flex items-center justify-center text-destructive">
            <span>{error}</span>
          </div>
        ) : data.length > 0 ? (
          <svg className="w-full h-48 mb-6" viewBox="0 0 800 200">
            {/* Grid lines */}
            <g stroke="hsl(var(--border))" strokeWidth="1" opacity="0.5">
              {[0, 1, 2, 3, 4].map((i) => (
                <line key={`h${i}`} x1="0" y1={i * 50} x2="800" y2={i * 50} />
              ))}
            </g>

            {/* Y-axis labels */}
            <text x="10" y="15" fontSize="12" fill="hsl(var(--muted-foreground))" textAnchor="end">
              10
            </text>
            <text x="10" y="65" fontSize="12" fill="hsl(var(--muted-foreground))" textAnchor="end">
              7.5
            </text>
            <text x="10" y="115" fontSize="12" fill="hsl(var(--muted-foreground))" textAnchor="end">
              5
            </text>
            <text x="10" y="165" fontSize="12" fill="hsl(var(--muted-foreground))" textAnchor="end">
              2.5
            </text>

            {/* Data line */}
            <polyline
              points={data
                .map((p, i) => {
                  const x = 30 + (i / (data.length - 1)) * 750;
                  const y = 200 - (p.score / 10) * 200;
                  return `${x},${y}`;
                })
                .join(' ')}
              fill="none"
              stroke="hsl(var(--accent))"
              strokeWidth="2"
            />

            {/* Data points */}
            {data.map((p, i) => {
              const x = 30 + (i / (data.length - 1)) * 750;
              const y = 200 - (p.score / 10) * 200;
              return (
                <circle key={`p${i}`} cx={x} cy={y} r="3" fill="hsl(var(--accent))" opacity="0.6" />
              );
            })}

            {/* X-axis label */}
            <text x="400" y="195" fontSize="12" fill="hsl(var(--muted-foreground))" textAnchor="middle">
              Time →
            </text>
          </svg>
        ) : (
          <div className="h-48 flex items-center justify-center text-muted-foreground">
            <span>No data available</span>
          </div>
        )}

        {/* Insights */}
        <div className="bg-muted rounded p-4 text-sm text-foreground">
          <div className="flex items-start gap-2">
            <TrendingUp size={16} className="flex-shrink-0 mt-0.5 text-accent" />
            <div>
              <div className="font-medium mb-1">24h Insight</div>
              {trend > 0 ? (
                <span>Score improving (+{trend.toFixed(2)}). Convergence stable.</span>
              ) : (
                <span>Score declining ({trend.toFixed(2)}). Check drift levels.</span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
