import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import type { CanaryView, Variant } from '@/lib/api/autonomous-forge';
import {
  STATUS_LABEL,
  TRIGGER_LABEL,
  VARIANT_COLOR,
  VARIANT_LABEL,
  effectiveTraffic,
  formatScore,
  sampleProgress,
  verdictLabel,
} from './autonomous-forge-encoding';

interface StatusDisplayProps {
  canary: CanaryView | null;
}

const VARIANTS: Variant[] = ['live', 'candidate'];

/** The running canary: who gets which body, and how each one is rated. */
export const StatusDisplay: React.FC<StatusDisplayProps> = ({ canary }) => {
  if (!canary) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">No active canary</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Forge a candidate for a skill below, or let the autopilot do it when users rate a skill low.
        </CardContent>
      </Card>
    );
  }
  const samples = sampleProgress(canary);
  const trigger = canary.trigger?.reason ? TRIGGER_LABEL[canary.trigger.reason] ?? canary.trigger.reason : null;

  return (
    <Card>
      <CardHeader className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base font-mono">{canary.skill_id}</CardTitle>
          <Badge variant={canary.status === 'ready' ? 'default' : 'secondary'}>
            {STATUS_LABEL[canary.status]}
          </Badge>
        </div>
        {trigger && <p className="text-xs text-muted-foreground">{trigger}</p>}
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-1.5">
          <div className="flex justify-between text-sm">
            <span>Chats served the candidate</span>
            <span className="font-medium tabular-nums">
              {effectiveTraffic(canary)}%
              {canary.status === 'paused' && (
                <span className="font-normal text-muted-foreground"> ({canary.traffic_percent}% when resumed)</span>
              )}
            </span>
          </div>
          <Progress value={effectiveTraffic(canary)} />
          <p className="text-xs text-muted-foreground">
            Steps {canary.gates.traffic_steps.join(' → ')}% — a chat keeps its variant for the whole canary.
          </p>
        </div>

        <table className="w-full text-sm" aria-label="Ratings per variant">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th className="font-normal pb-1">Variant</th>
              <th className="font-normal pb-1 text-right">Turns served</th>
              <th className="font-normal pb-1 text-right">Used</th>
              <th className="font-normal pb-1 text-right">Rated</th>
              <th className="font-normal pb-1 text-right">Mean rating</th>
            </tr>
          </thead>
          <tbody>
            {VARIANTS.map((v) => (
              <tr key={v} className="border-t">
                <td className="py-1.5">
                  <span className="inline-flex items-center gap-2">
                    <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: VARIANT_COLOR[v] }} />
                    {VARIANT_LABEL[v]}
                  </span>
                </td>
                <td className="py-1.5 text-right tabular-nums">{canary.stats[v].served_n}</td>
                <td className="py-1.5 text-right tabular-nums">{canary.stats[v].usage_n}</td>
                <td className="py-1.5 text-right tabular-nums">{canary.stats[v].outcome_n}</td>
                <td className="py-1.5 text-right tabular-nums font-medium">{formatScore(canary.stats[v].outcome_mean)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="rounded-md border p-3 text-sm space-y-1">
          <div className="flex justify-between gap-4">
            <span className="text-muted-foreground">Compared against</span>
            <span className="tabular-nums">
              {formatScore(canary.reference.mean)}{' '}
              <span className="text-xs text-muted-foreground">
                ({canary.reference.source === 'live_during_canary'
                  ? 'live, during this canary'
                  : canary.reference.source === 'live_before_canary'
                    ? 'live, before this canary'
                    : 'no rating yet'})
              </span>
            </span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-muted-foreground">Candidate ratings</span>
            <span className="tabular-nums">
              {samples.have} <span className="text-xs text-muted-foreground">(min. {samples.need})</span>
            </span>
          </div>
          <p className="pt-1 font-medium" data-testid="canary-verdict">{verdictLabel(canary)}</p>
          <p className="text-xs text-muted-foreground">
            Ratings come from the user&apos;s next message: approval 0.9, rephrase 0.3, rejection 0.1.
          </p>
        </div>
      </CardContent>
    </Card>
  );
};

export default StatusDisplay;
