/**
 * StatusBadge Component
 *
 * Displays status (no_calls | low_activity | high_activity) with color coding.
 */

import React from 'react';
import { cn } from '@/lib/utils';

interface StatusBadgeProps {
  status: 'no_calls' | 'low_activity' | 'high_activity';
}

const STATUS_CONFIG = {
  no_calls: {
    label: 'No Activity',
    classes: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-l-emerald-500',
    description: 'No deprecated API usage detected',
  },
  low_activity: {
    label: 'Low Activity',
    classes: 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-l-amber-500',
    description: 'Deprecated APIs used <10/min (acceptable)',
  },
  high_activity: {
    label: 'High Activity',
    classes: 'bg-destructive/15 text-destructive border-l-destructive',
    description: 'Deprecated APIs used >10/min (investigate)',
  },
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const config = STATUS_CONFIG[status];

  return (
    <div className="flex flex-col gap-2">
      <div className={cn('px-3 py-2 rounded-lg text-center font-semibold text-sm border-l-[3px]', config.classes)}>
        {config.label}
      </div>
      <div className="text-xs text-muted-foreground">{config.description}</div>
    </div>
  );
}
