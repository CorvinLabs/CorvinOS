import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Lock, Network, AlertTriangle } from 'lucide-react';
import type { VibeData, ContextLayer } from '../hooks/useVibeData';

interface Props {
  data: VibeData;
  onQualityGateChange?: (policy: 'tier_1' | 'tier_2' | 'tier_3') => void;
}

export function ContextIntelligence({ data, onQualityGateChange }: Props) {
  const entropy = data.pipeline_context.entropy_score || 0;
  const entropyColor =
    entropy < 0.3 ? 'bg-green-500' : entropy < 0.6 ? 'bg-yellow-500' : 'bg-red-500';

  return (
    <div className="space-y-4">
      {/* Original Context */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2">
              <Lock className="h-4 w-4" />
              Original Context
            </CardTitle>
            <Badge variant="outline" className="text-xs">Immutable</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          <div>
            <p className="text-xs text-muted-foreground mb-1">Task</p>
            <p className="text-sm line-clamp-2">
              {data.original_context.task_description || 'No task'}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground mb-1">Intent</p>
            <p className="text-sm line-clamp-2">
              {data.original_context.user_intent || 'No intent'}
            </p>
          </div>
          <div className="flex items-center justify-between pt-2 border-t">
            <span className="text-xs text-muted-foreground">Hash</span>
            <Badge variant={data.original_context.is_valid ? 'default' : 'secondary'}>
              {data.original_context.is_valid ? '✓ Valid' : '✗ Invalid'}
            </Badge>
          </div>
        </CardContent>
      </Card>

      {/* Pipeline Context */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Network className="h-4 w-4" />
            Pipeline Context
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {/* Tier Breakdown */}
          <div className="grid grid-cols-3 gap-2">
            <div className="bg-secondary/50 rounded p-2 text-center">
              <p className="text-2xl font-bold text-emerald-600">
                {data.pipeline_context.tier_1_count}
              </p>
              <p className="text-xs text-muted-foreground">TIER_1</p>
            </div>
            <div className="bg-secondary/50 rounded p-2 text-center">
              <p className="text-2xl font-bold text-amber-600">
                {data.pipeline_context.tier_2_count}
              </p>
              <p className="text-xs text-muted-foreground">TIER_2</p>
            </div>
            <div className="bg-secondary/50 rounded p-2 text-center">
              <p className="text-2xl font-bold text-slate-600">
                {data.pipeline_context.tier_3_count}
              </p>
              <p className="text-xs text-muted-foreground">TIER_3</p>
            </div>
          </div>

          {/* Entropy Gauge */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">Entropy</span>
              <span className="text-xs font-mono font-bold">
                {Math.round(entropy * 100)}%
              </span>
            </div>
            <div className="w-full bg-secondary rounded-full h-3">
              <div
                className={`${entropyColor} h-3 rounded-full transition-all`}
                style={{ width: `${entropy * 100}%` }}
              />
            </div>
            {entropy > 0.6 && (
              <p className="text-xs text-red-600 flex items-center gap-1 mt-1">
                <AlertTriangle className="h-3 w-3" />
                High contradiction risk
              </p>
            )}
          </div>

          {/* Recent Additions */}
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Recent Additions</p>
            <div className="space-y-1 max-h-24 overflow-y-auto">
              {data.pipeline_context.recent_additions.length === 0 ? (
                <p className="text-xs text-muted-foreground italic">No additions yet</p>
              ) : (
                data.pipeline_context.recent_additions.slice(0, 3).map((add: ContextLayer, i: number) => (
                  <div key={i} className="bg-secondary/30 rounded p-1.5 text-xs">
                    <div className="flex items-center justify-between mb-0.5">
                      <Badge variant="outline" className="text-[10px]">
                        {add.tier}
                      </Badge>
                      <span className="text-muted-foreground text-[10px]">
                        {Math.round(add.confidence * 100)}%
                      </span>
                    </div>
                    <p className="line-clamp-2">{add.text}</p>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Quality Gate */}
          <div className="pt-2 border-t space-y-1">
            <p className="text-xs text-muted-foreground">Quality Gate</p>
            <div className="flex gap-1">
              {(['tier_1', 'tier_2', 'tier_3'] as const).map(tier => (
                <button
                  key={tier}
                  onClick={() => onQualityGateChange?.(tier)}
                  className={`flex-1 rounded px-2 py-1 text-xs font-medium transition-colors ${
                    data.quality_gate_policy === tier
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
                  }`}
                >
                  {tier}
                </button>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
