/**
 * Score Header — Overall maturity score (0-10) + status badge
 */

import React from 'react';
import { MaturityData } from './types';

interface ScoreHeaderProps {
  data: MaturityData;
}

export function ScoreHeader({ data }: ScoreHeaderProps) {
  const getStatusColor = (status: string): string => {
    switch (status) {
      case 'DEAD': return 'text-destructive';
      case 'NASCENT': return 'text-orange-600 dark:text-orange-400';
      case 'LEARNING': return 'text-amber-600 dark:text-amber-400';
      case 'MATURE': return 'text-emerald-600 dark:text-emerald-400';
      case 'OPTIMIZED': return 'text-accent';
      case 'FULLY TRAINED': return 'text-purple-600 dark:text-purple-400';
      default: return 'text-muted-foreground';
    }
  };

  const getBgColor = (status: string): string => {
    switch (status) {
      case 'DEAD': return 'bg-destructive/10';
      case 'NASCENT': return 'bg-orange-500/10';
      case 'LEARNING': return 'bg-amber-500/10';
      case 'MATURE': return 'bg-emerald-500/10';
      case 'OPTIMIZED': return 'bg-accent/10';
      case 'FULLY TRAINED': return 'bg-purple-500/10';
      default: return 'bg-muted';
    }
  };

  return (
    <div className="bg-card border border-border rounded-lg p-8">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between">
        {/* Score Display */}
        <div className="flex items-baseline gap-4">
          <div className="flex items-baseline">
            <span className="text-6xl font-bold bg-gradient-to-r from-accent to-accent/60 bg-clip-text text-transparent">
              {data.overallScore}
            </span>
            <span className="text-2xl text-muted-foreground ml-2">/10</span>
          </div>

          {/* Status Badge */}
          <div className={`px-4 py-2 rounded-lg ${getBgColor(data.status)}`}>
            <div className={`text-sm font-semibold ${getStatusColor(data.status)}`}>
              {data.status}
            </div>
            <div className="text-xs text-muted-foreground mt-1">
              {data.trend.direction === 'up' && `↑ +${data.trend.value}`}
              {data.trend.direction === 'down' && `↓ -${data.trend.value}`}
              {data.trend.direction === 'stable' && '→ stable'}
            </div>
          </div>
        </div>

        {/* Right Side Info */}
        <div className="mt-6 lg:mt-0 grid grid-cols-2 gap-6">
          <div>
            <div className="text-xs text-muted-foreground uppercase font-semibold">
              Last Updated
            </div>
            <div className="text-sm text-foreground mt-1">
              {new Date(data.lastUpdated).toLocaleTimeString()}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground uppercase font-semibold">
              30-Day Projection
            </div>
            <div className="text-sm text-accent font-semibold mt-1">
              {data.projection}/10
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
