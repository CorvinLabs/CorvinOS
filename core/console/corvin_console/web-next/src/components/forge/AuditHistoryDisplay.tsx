import React, { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { CanaryView } from '@/lib/api/autonomous-forge';
import { STATUS_LABEL, formatScore } from './autonomous-forge-encoding';

interface AuditHistoryDisplayProps {
  attempts: CanaryView[];
}

const EVENT_LABEL: Record<string, string> = {
  started: 'Canary started',
  traffic: 'Traffic changed',
  paused: 'Paused',
  canary: 'Resumed',
  ready: 'Ready for approval',
  approved: 'Approved',
  deferred: 'Deferred',
  rolled_back: 'Rolled back',
};

const when = (ts: number) => new Date(ts * 1000).toLocaleString('en-US');

/** Every canary with its lifecycle, each step linked to its audit-chain record. */
export const AuditHistoryDisplay: React.FC<AuditHistoryDisplayProps> = ({ attempts }) => {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">History</CardTitle>
      </CardHeader>
      <CardContent>
        {attempts.length === 0 ? (
          <p className="text-sm text-muted-foreground">No canary has run yet.</p>
        ) : (
          <ul className="divide-y">
            {attempts.map((a) => (
              <li key={a.canary_id} className="py-2">
                <button
                  className="flex w-full items-center gap-2 text-left text-sm"
                  onClick={() => setOpen(open === a.canary_id ? null : a.canary_id)}
                  aria-expanded={open === a.canary_id}
                >
                  {open === a.canary_id ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                  <span className="font-mono">{a.skill_id}</span>
                  <Badge variant="outline">{STATUS_LABEL[a.status]}</Badge>
                  <span className="ml-auto text-xs text-muted-foreground">{when(a.created_at)}</span>
                </button>
                {open === a.canary_id && (
                  <div className="mt-2 ml-6 space-y-2 text-xs">
                    <p className="text-muted-foreground">
                      {a.source === 'autopilot' ? 'Autopilot' : 'Operator'} · review quality {formatScore(a.quality)} ·
                      candidate {formatScore(a.stats.candidate.outcome_mean)} over {a.stats.candidate.outcome_n} ratings ·
                      live {formatScore(a.stats.live.outcome_mean)} over {a.stats.live.outcome_n}
                    </p>
                    <ol className="space-y-1">
                      {(a.events ?? []).map((e, i) => (
                        <li key={i} className="flex flex-wrap gap-x-3">
                          <span className="tabular-nums text-muted-foreground">{when(e.ts)}</span>
                          <span>
                            {EVENT_LABEL[e.type] ?? e.type}
                            {e.type === 'traffic' ? ` → ${String(e.traffic_percent)}%` : ''}
                            {e.actor ? ` (${String(e.actor)})` : ''}
                          </span>
                          <span className="font-mono text-muted-foreground" title="Audit-chain record hash">
                            {e.hash ? e.hash.slice(0, 12) : 'no chain record'}
                          </span>
                        </li>
                      ))}
                    </ol>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
};

export default AuditHistoryDisplay;
