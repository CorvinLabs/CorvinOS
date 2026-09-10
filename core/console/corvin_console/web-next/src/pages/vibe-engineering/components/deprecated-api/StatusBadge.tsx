/**
 * StatusBadge Component
 *
 * Displays status (no_calls | low_activity | high_activity) with color coding.
 */

import React from 'react';

interface StatusBadgeProps {
  status: 'no_calls' | 'low_activity' | 'high_activity';
}

const STATUS_CONFIG = {
  no_calls: {
    label: 'No Activity',
    color: '#3FB950', // Green
    description: 'No deprecated API usage detected',
  },
  low_activity: {
    label: 'Low Activity',
    color: '#D29922', // Yellow
    description: 'Deprecated APIs used <10/min (acceptable)',
  },
  high_activity: {
    label: 'High Activity',
    color: '#F85149', // Red
    description: 'Deprecated APIs used >10/min (investigate)',
  },
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const config = STATUS_CONFIG[status];

  return (
    <div className="flex flex-col gap-2">
      <div
        className="px-3 py-2 rounded-lg text-center font-semibold text-sm"
        style={{
          backgroundColor: `${config.color}20`,
          color: config.color,
          borderLeft: `3px solid ${config.color}`,
        }}
      >
        {config.label}
      </div>
      <div className="text-xs text-[#8B949E]">{config.description}</div>
    </div>
  );
}
