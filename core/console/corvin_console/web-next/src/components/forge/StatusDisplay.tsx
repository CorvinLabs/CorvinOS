import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { AlertCircle, CheckCircle, Clock, Zap } from 'lucide-react';

export interface CanaryState {
  skill_id: string;
  version: string;
  status: 'canary' | 'approved' | 'deferred' | 'paused' | 'rolled_back';
  confidence: number;
  latency_p95_ms: number;
  error_rate: number;
  traffic_percent: number;
  time_remaining_sec: number;
  validation_passed: boolean;
}

interface StatusDisplayProps {
  canaryState: CanaryState | null;
  loading?: boolean;
}

/**
 * StatusDisplay: Shows current canary state with color-coded status
 */
export const StatusDisplay: React.FC<StatusDisplayProps> = ({
  canaryState,
  loading = false,
}) => {
  if (!canaryState) {
    return (
      <Card>
        <CardContent className="py-6 text-center text-muted-foreground">
          {loading ? 'Loading canary state...' : 'No active canary'}
        </CardContent>
      </Card>
    );
  }

  const isHealthy = canaryState.error_rate < 5 && canaryState.latency_p95_ms < 1500;
  const isDegraded = canaryState.error_rate < 10 && canaryState.latency_p95_ms < 2000;
  const getStatusColor = () => {
    if (isHealthy) return 'bg-green-500/10 text-green-700 dark:text-green-400';
    if (isDegraded) return 'bg-yellow-500/10 text-yellow-700 dark:text-yellow-400';
    return 'bg-red-500/10 text-red-700 dark:text-red-400';
  };

  const getStatusIcon = () => {
    if (isHealthy) return <CheckCircle className="w-5 h-5" />;
    if (isDegraded) return <AlertCircle className="w-5 h-5" />;
    return <AlertCircle className="w-5 h-5" />;
  };

  const formatTime = (seconds: number) => {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    return `${Math.floor(seconds / 3600)}h`;
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle>Canary Status</CardTitle>
          <Badge
            className={`${getStatusColor()} border-0`}
            variant="secondary"
          >
            {canaryState.status === 'canary' ? 'Active' : canaryState.status}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Skill info */}
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">Skill</span>
          <span className="font-semibold">{canaryState.skill_id}</span>
        </div>

        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">Version</span>
          <span className="font-mono text-xs bg-secondary px-2 py-1 rounded">
            {canaryState.version}
          </span>
        </div>

        {/* Health indicators */}
        <div className="grid grid-cols-3 gap-2 pt-2 border-t">
          <div className="text-center">
            <div className="text-xs text-muted-foreground mb-1">Confidence</div>
            <div className="text-lg font-bold">
              {(canaryState.confidence * 100).toFixed(0)}%
            </div>
          </div>

          <div className="text-center">
            <div className="text-xs text-muted-foreground mb-1">Traffic</div>
            <div className="text-lg font-bold">{canaryState.traffic_percent}%</div>
          </div>

          <div className="text-center">
            <div className="text-xs text-muted-foreground mb-1">Time Left</div>
            <div className="text-lg font-bold">
              {formatTime(canaryState.time_remaining_sec)}
            </div>
          </div>
        </div>

        {/* Metrics grid */}
        <div className="grid grid-cols-2 gap-2 pt-2 border-t">
          <div className="flex items-start gap-2">
            <Zap className={`w-4 h-4 mt-0.5 ${isHealthy ? 'text-green-500' : isDegraded ? 'text-yellow-500' : 'text-red-500'}`} />
            <div>
              <div className="text-xs text-muted-foreground">Error Rate</div>
              <div className="font-semibold">{canaryState.error_rate.toFixed(2)}%</div>
            </div>
          </div>

          <div className="flex items-start gap-2">
            <Clock className={`w-4 h-4 mt-0.5 ${isHealthy ? 'text-green-500' : isDegraded ? 'text-yellow-500' : 'text-red-500'}`} />
            <div>
              <div className="text-xs text-muted-foreground">P95 Latency</div>
              <div className="font-semibold">{canaryState.latency_p95_ms}ms</div>
            </div>
          </div>
        </div>

        {/* Validation status */}
        <div className="flex items-center gap-2 pt-2 border-t text-sm">
          {getStatusIcon()}
          <span>
            {canaryState.validation_passed ? (
              <span className="text-green-600 dark:text-green-400">
                All validations passed
              </span>
            ) : (
              <span className="text-red-600 dark:text-red-400">
                Validation issues detected
              </span>
            )}
          </span>
        </div>
      </CardContent>
    </Card>
  );
};

export default StatusDisplay;
