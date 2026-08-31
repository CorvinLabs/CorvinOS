import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Brain, Clock } from 'lucide-react';
import type { VibeData, WorkerStatus, Decision } from '../hooks/useVibeData';

interface Props {
  data: VibeData;
}

function statusIcon(status: string): React.ReactNode {
  switch (status) {
    case 'running':
      return <span className="inline-block h-2 w-2 rounded-full bg-green-500 animate-pulse" />;
    case 'thinking':
      return <span className="inline-block h-2 w-2 rounded-full bg-yellow-500 animate-pulse" />;
    case 'blocked':
      return <span className="inline-block h-2 w-2 rounded-full bg-red-500" />;
    default:
      return <span className="inline-block h-2 w-2 rounded-full bg-gray-500" />;
  }
}

export function BrainStatus({ data }: Props) {
  const elapsedMinutes = Math.floor(data.active_task?.elapsed_seconds || 0 / 60);
  const elapsedSeconds = (data.active_task?.elapsed_seconds || 0) % 60;

  return (
    <div className="space-y-4">
      {/* Active Task */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Brain className="h-4 w-4" />
            Active Task
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="font-medium text-base truncate">
            {data.active_task?.title || 'No active task'}
          </p>
          <div className="flex items-center justify-between">
            <Badge variant="outline" className="text-xs">
              {data.active_task?.phase || 'idle'}
            </Badge>
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <Clock className="h-3 w-3" />
              {elapsedMinutes}m {elapsedSeconds}s
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Worker Status */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Worker Status</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {data.workers.map((worker: WorkerStatus) => (
            <div key={worker.name} className="flex items-center justify-between text-sm">
              <div className="flex items-center gap-2">
                {statusIcon(worker.status)}
                <span className="text-muted-foreground">{worker.name}</span>
              </div>
              <Badge variant="secondary" className="text-xs capitalize">
                {worker.status}
              </Badge>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Decision Queue */}
      {data.decision_queue.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Current Decision</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-sm font-medium">{data.decision_queue[0]?.type}</p>
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">Confidence</span>
              <span className="text-xs font-mono">
                {Math.round((data.decision_queue[0]?.confidence || 0) * 100)}%
              </span>
            </div>
            <div className="w-full bg-secondary rounded-full h-2">
              <div
                className="bg-green-500 h-2 rounded-full transition-all"
                style={{
                  width: `${Math.round((data.decision_queue[0]?.confidence || 0) * 100)}%`,
                }}
              />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Recent Decisions */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Recent Decisions</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2 max-h-40 overflow-y-auto">
            {data.recent_decisions.length === 0 ? (
              <p className="text-xs text-muted-foreground italic">No decisions yet</p>
            ) : (
              data.recent_decisions.slice(0, 5).map((decision: Decision, i: number) => (
                <div key={i} className="text-xs flex justify-between border-b pb-1 last:border-b-0">
                  <span className="text-muted-foreground">{decision.type}</span>
                  <Badge
                    variant={decision.outcome === 'success' ? 'default' : 'secondary'}
                    className="text-xs"
                  >
                    {decision.outcome || 'pending'}
                  </Badge>
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
