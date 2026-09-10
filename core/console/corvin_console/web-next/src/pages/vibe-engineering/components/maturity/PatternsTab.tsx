/**
 * Patterns Tab — Top Routing Decisions, Top Skills, Context Distribution
 * Phase 4a Operator View
 */

import React, { useMemo } from 'react';
import { BarChart3, Zap, FolderOpen } from 'lucide-react';

interface PatternsTabProps {
  loopScores: any;
}

// Mock data for phase 4a — will connect to real telemetry in Phase 4b
const MOCK_ROUTING_PATTERNS = [
  { engine: 'Claude Opus', count: 245, percentage: 58, trend: '+12%' },
  { engine: 'Claude Sonnet', count: 142, percentage: 34, trend: '+5%' },
  { engine: 'Claude Haiku', count: 38, percentage: 9, trend: '−3%' },
];

const MOCK_SKILL_PATTERNS = [
  { skill: 'os.delegation_router', count: 318, percentage: 42, category: 'routing' },
  { skill: 'loop-driven-engineering', count: 156, percentage: 21, category: 'ldd' },
  { skill: 'context_adapter_l10', count: 124, percentage: 16, category: 'context' },
  { skill: 'e2e_wiring_proof', count: 89, percentage: 12, category: 'testing' },
  { skill: 'root-cause-by-layer', count: 45, percentage: 6, category: 'debugging' },
];

const MOCK_CONTEXT_BUCKETS = [
  { bucket: 'lightweight (<2KB)', count: 412, percentage: 55 },
  { bucket: 'normal (2–5KB)', count: 268, percentage: 36 },
  { bucket: 'large (5–10KB)', count: 62, percentage: 8 },
  { bucket: 'heavy (>10KB)', count: 8, percentage: 1 },
];

export function PatternsTab({ loopScores }: PatternsTabProps) {
  return (
    <div className="space-y-6">
      {/* Top Routing Decisions */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-sm font-semibold mb-4 text-foreground flex items-center gap-2">
          <Zap size={18} className="text-accent" />
          Top Routing Decisions (7d)
        </h3>
        <div className="space-y-4">
          {MOCK_ROUTING_PATTERNS.map((pattern, i) => (
            <div key={i}>
              <div className="flex items-center justify-between mb-2">
                <div>
                  <span className="text-sm font-medium text-foreground">{pattern.engine}</span>
                  <span className="text-xs text-muted-foreground ml-2">({pattern.count} calls)</span>
                </div>
                <span className={`text-xs font-semibold ${pattern.trend.startsWith('+') ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}`}>
                  {pattern.trend}
                </span>
              </div>
              <div className="w-full bg-muted rounded-full h-2 overflow-hidden">
                <div
                  className="bg-accent h-2 rounded-full"
                  style={{ width: `${pattern.percentage}%` }}
                ></div>
              </div>
              <div className="text-xs text-muted-foreground mt-1">{pattern.percentage}% of all routing decisions</div>
            </div>
          ))}
        </div>
      </div>

      {/* Top Skills Called */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-sm font-semibold mb-4 text-foreground flex items-center gap-2">
          <BarChart3 size={18} className="text-accent" />
          Top Skills Used (7d)
        </h3>
        <div className="space-y-3">
          {MOCK_SKILL_PATTERNS.map((skill, i) => (
            <div key={i} className="flex items-center justify-between p-3 bg-muted rounded border border-border">
              <div className="flex-1">
                <div className="text-sm font-medium text-foreground">{skill.skill}</div>
                <div className="text-xs text-muted-foreground mt-0.5">
                  {skill.count} invocations • {skill.category}
                </div>
              </div>
              <div className="text-right">
                <div className="text-sm font-semibold text-accent">{skill.percentage}%</div>
                <div className="w-16 h-1.5 bg-card rounded mt-1 overflow-hidden">
                  <div
                    className="bg-accent h-1.5 rounded"
                    style={{ width: `${skill.percentage}%` }}
                  ></div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Context Distribution */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-sm font-semibold mb-4 text-foreground flex items-center gap-2">
          <FolderOpen size={18} className="text-accent" />
          Context Size Distribution (7d)
        </h3>
        <div className="space-y-4">
          {MOCK_CONTEXT_BUCKETS.map((bucket, i) => (
            <div key={i}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm text-foreground">{bucket.bucket}</span>
                <span className="text-xs font-semibold text-muted-foreground">{bucket.count} ({bucket.percentage}%)</span>
              </div>
              <div className="w-full bg-muted rounded-full h-2 overflow-hidden">
                <div
                  className={`h-2 rounded-full ${
                    bucket.percentage > 50
                      ? 'bg-emerald-500'
                      : bucket.percentage > 30
                        ? 'bg-accent'
                        : bucket.percentage > 10
                          ? 'bg-amber-500'
                          : 'bg-destructive'
                  }`}
                  style={{ width: `${bucket.percentage}%` }}
                ></div>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4 p-3 bg-muted rounded border border-border">
          <div className="text-xs text-muted-foreground">
            💡 <strong>Insight:</strong> Most requests use lightweight context (&lt;2KB). Heavy contexts (&gt;10KB) are rare — consider trimming if they appear.
          </div>
        </div>
      </div>

      {/* Pattern Insights */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-sm font-semibold mb-4 text-foreground">📌 Pattern Insights</h3>
        <ul className="space-y-3 text-sm text-foreground">
          <li className="flex gap-3">
            <span className="text-emerald-600 dark:text-emerald-400 flex-shrink-0">✓</span>
            <span>Opus dominance (58%) is expected for complex reasoning tasks — monitor if percentage drops below 50%</span>
          </li>
          <li className="flex gap-3">
            <span className="text-emerald-600 dark:text-emerald-400 flex-shrink-0">✓</span>
            <span>Skill diversification is healthy — top 5 skills cover only 97% of calls (no single point of failure)</span>
          </li>
          <li className="flex gap-3">
            <span className="text-accent flex-shrink-0">→</span>
            <span>Context sizes are well-distributed — lightweight (&lt;2KB) usage suggests good context efficiency</span>
          </li>
        </ul>
      </div>
    </div>
  );
}
