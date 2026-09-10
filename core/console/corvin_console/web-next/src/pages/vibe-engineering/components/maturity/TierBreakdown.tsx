/**
 * Tier Breakdown — Cards showing Tier 1 and Tier 2 loop scores
 */

import React from 'react';
import { LoopScores, getScoreColor, getScoreLabel } from './types';

interface TierBreakdownProps {
  loopScores: LoopScores;
}

const TIER1_INFO = [
  { key: 'confidence', label: 'Confidence Loop', icon: '⭐' },
  { key: 'routing', label: 'Routing Loop', icon: '🛣️' },
  { key: 'context', label: 'Context Loop', icon: '📚' },
  { key: 'workflow', label: 'Workflow Loop', icon: '⚙️' },
  { key: 'data_flow', label: 'Data Flow Loop', icon: '🌊' },
  { key: 'security', label: 'Security Loop', icon: '🔒' },
] as const;

const TIER2_INFO = [
  { key: 'memory', label: 'Memory Loop', icon: '🧠' },
  { key: 'skills', label: 'Skills Loop', icon: '🎯' },
  { key: 'plugins', label: 'Plugins Loop', icon: '🔌' },
  { key: 'audit', label: 'Audit Loop', icon: '📋' },
  { key: 'compliance', label: 'Compliance Loop', icon: '✅' },
  { key: 'system', label: 'System Loop', icon: '🖥️' },
] as const;

function LoopBar({
  label,
  score,
  icon,
}: {
  label: string;
  score: number;
  icon: string;
}) {
  const color = getScoreColor(score);
  const status = getScoreLabel(score);
  const percentage = (score / 10) * 100;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium text-foreground">
          {icon} {label}
        </span>
        <span className="font-semibold text-foreground">{score.toFixed(1)}</span>
      </div>
      <div className="flex items-center gap-2">
        <div className="flex-1 h-2 bg-background rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{
              width: `${percentage}%`,
              backgroundColor: color,
              opacity: 0.8,
            }}
          />
        </div>
        <span className="text-xs text-muted-foreground w-12 text-right">{status}</span>
      </div>
    </div>
  );
}

export function TierBreakdown({ loopScores }: TierBreakdownProps) {
  const tier1Score = [
    loopScores.confidence,
    loopScores.routing,
    loopScores.context,
    loopScores.workflow,
    loopScores.data_flow,
    loopScores.security,
  ].reduce((a, b) => a + b, 0) / 6;

  const tier2Score = [
    loopScores.memory,
    loopScores.skills,
    loopScores.plugins,
    loopScores.audit,
    loopScores.compliance,
    loopScores.system,
  ].reduce((a, b) => a + b, 0) / 6;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* Tier 1: Core Loops */}
      <div className="bg-card border border-border rounded-lg p-6">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-sm font-semibold text-foreground">
            Tier 1: Core Loops
          </h3>
          <div className="text-right">
            <div className="text-2xl font-bold text-accent">
              {tier1Score.toFixed(1)}
            </div>
            <div className="text-xs text-muted-foreground">Average</div>
          </div>
        </div>

        <div className="space-y-4">
          {TIER1_INFO.map((info) => (
            <LoopBar
              key={info.key}
              label={info.label}
              score={loopScores[info.key as keyof LoopScores]}
              icon={info.icon}
            />
          ))}
        </div>

        {/* Weight */}
        <div className="mt-6 pt-6 border-t border-border">
          <div className="text-xs text-muted-foreground mb-2">Weight in Overall Score</div>
          <div className="text-sm font-semibold text-accent">40%</div>
        </div>
      </div>

      {/* Tier 2: Infrastructure Loops */}
      <div className="bg-card border border-border rounded-lg p-6">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-sm font-semibold text-foreground">
            Tier 2: Infrastructure
          </h3>
          <div className="text-right">
            <div className="text-2xl font-bold text-accent">
              {tier2Score.toFixed(1)}
            </div>
            <div className="text-xs text-muted-foreground">Average</div>
          </div>
        </div>

        <div className="space-y-4">
          {TIER2_INFO.map((info) => (
            <LoopBar
              key={info.key}
              label={info.label}
              score={loopScores[info.key as keyof LoopScores]}
              icon={info.icon}
            />
          ))}
        </div>

        {/* Weight */}
        <div className="mt-6 pt-6 border-t border-border">
          <div className="text-xs text-muted-foreground mb-2">Weight in Overall Score</div>
          <div className="text-sm font-semibold text-accent">35%</div>
        </div>
      </div>
    </div>
  );
}
