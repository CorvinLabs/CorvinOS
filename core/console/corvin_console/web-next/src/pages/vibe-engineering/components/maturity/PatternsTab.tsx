/**
 * Patterns Tab — REAL usage patterns (routing engine choices + skill volume).
 *
 * All data comes from `/v1/console/vibe/maturity/patterns`, which derives it on
 * demand from the ADR-0314 learning EventStore (SKILL_EXECUTED events):
 *   • Top Routing Decisions = the delegation router's engine choices
 *     (shadow-mode `output.engine` on os.delegation_router executions).
 *   • Top Skills Used = execution volume per skill_id, with success rate.
 * There is NO context-size distribution and NO fabricated trend — nothing
 * measures those cross-platform, so they are omitted rather than mocked. When
 * the window has no executions, an honest empty state renders.
 */

import React, { useEffect, useState } from 'react';
import { BarChart3, Zap, Database } from 'lucide-react';
import type { TimeWindow } from '../../hooks/useLiveMaturityData';

interface RoutingPattern {
  engine: string;
  count: number;
  percentage: number;
}

interface SkillPattern {
  skill: string;
  count: number;
  percentage: number;
  success_rate: number | null;
}

interface PatternsResponse {
  window: string;
  generated_at: string;
  available: boolean;
  routing: RoutingPattern[];
  skills: SkillPattern[];
  total_routing_decisions: number;
  total_skill_executions: number;
}

interface PatternsTabProps {
  loopScores?: unknown; // kept for call-site compatibility; patterns are fetched live
  window?: TimeWindow;
}

export function PatternsTab({ window: windowPref = '7d' }: PatternsTabProps) {
  const [data, setData] = useState<PatternsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        const res = await fetch(`/v1/console/vibe/maturity/patterns?window=${windowPref}`, {
          headers: { 'Content-Type': 'application/json' },
        });
        if (!res.ok) throw new Error(`API returned ${res.status}`);
        const json: PatternsResponse = await res.json();
        if (!cancelled) {
          setData(json);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Unknown error');
          setData(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const interval = setInterval(load, 60 * 1000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [windowPref]);

  const hasRouting = (data?.routing?.length ?? 0) > 0;
  const hasSkills = (data?.skills?.length ?? 0) > 0;

  if (loading && !data) {
    return (
      <div className="text-center py-12 text-sm text-muted-foreground">Loading usage patterns…</div>
    );
  }

  if (error) {
    return (
      <div className="border border-destructive/40 bg-destructive/10 rounded p-4 text-sm text-destructive">
        {error}
      </div>
    );
  }

  if (!hasRouting && !hasSkills) {
    return (
      <div className="border border-border bg-card rounded-lg p-8 text-center">
        <Database size={28} className="mx-auto mb-3 text-muted-foreground" />
        <div className="text-sm font-medium text-foreground mb-1">No usage patterns yet</div>
        <div className="text-xs text-muted-foreground max-w-md mx-auto">
          Routing decisions and skill executions are read from the learning EventStore. Once the
          system routes tasks and runs skills in this window, the patterns populate automatically.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Top Routing Decisions */}
      {hasRouting && (
        <div className="bg-card border border-border rounded-lg p-6">
          <h3 className="text-sm font-semibold mb-1 text-foreground flex items-center gap-2">
            <Zap size={18} className="text-accent" />
            Top Routing Decisions ({data!.window})
          </h3>
          <div className="text-xs text-muted-foreground mb-4">
            {data!.total_routing_decisions} router decisions (delegation router, shadow mode)
          </div>
          <div className="space-y-4">
            {data!.routing.map((pattern) => (
              <div key={pattern.engine}>
                <div className="flex items-center justify-between mb-2">
                  <div>
                    <span className="text-sm font-medium text-foreground">{pattern.engine}</span>
                    <span className="text-xs text-muted-foreground ml-2">({pattern.count} decisions)</span>
                  </div>
                  <span className="text-xs font-semibold text-accent">{pattern.percentage}%</span>
                </div>
                <div className="w-full bg-muted rounded-full h-2 overflow-hidden">
                  <div className="bg-accent h-2 rounded-full" style={{ width: `${pattern.percentage}%` }}></div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Top Skills Used */}
      {hasSkills && (
        <div className="bg-card border border-border rounded-lg p-6">
          <h3 className="text-sm font-semibold mb-1 text-foreground flex items-center gap-2">
            <BarChart3 size={18} className="text-accent" />
            Top Skills Used ({data!.window})
          </h3>
          <div className="text-xs text-muted-foreground mb-4">
            {data!.total_skill_executions} skill executions
          </div>
          <div className="space-y-3">
            {data!.skills.map((skill) => (
              <div key={skill.skill} className="flex items-center justify-between p-3 bg-muted rounded border border-border">
                <div className="flex-1">
                  <div className="text-sm font-medium text-foreground">{skill.skill}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {skill.count} invocations
                    {skill.success_rate != null && ` • ${(skill.success_rate * 100).toFixed(0)}% success`}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-sm font-semibold text-accent">{skill.percentage}%</div>
                  <div className="w-16 h-1.5 bg-card rounded mt-1 overflow-hidden">
                    <div className="bg-accent h-1.5 rounded" style={{ width: `${skill.percentage}%` }}></div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
