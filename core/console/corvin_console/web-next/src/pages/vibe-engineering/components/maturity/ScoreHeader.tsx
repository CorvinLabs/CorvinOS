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
      case 'DEAD': return 'text-[#F85149]';
      case 'NASCENT': return 'text-[#FB8500]';
      case 'LEARNING': return 'text-[#D29922]';
      case 'MATURE': return 'text-[#3FB950]';
      case 'OPTIMIZED': return 'text-[#58A6FF]';
      case 'FULLY TRAINED': return 'text-[#A371F7]';
      default: return 'text-[#8B949E]';
    }
  };

  const getBgColor = (status: string): string => {
    switch (status) {
      case 'DEAD': return 'bg-[#3d1f1a]';
      case 'NASCENT': return 'bg-[#3d2817]';
      case 'LEARNING': return 'bg-[#3d3220]';
      case 'MATURE': return 'bg-[#1a3a1f]';
      case 'OPTIMIZED': return 'bg-[#1a2d3a]';
      case 'FULLY TRAINED': return 'bg-[#2d1f3a]';
      default: return 'bg-[#1a1f26]';
    }
  };

  return (
    <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-8">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between">
        {/* Score Display */}
        <div className="flex items-baseline gap-4">
          <div className="flex items-baseline">
            <span className="text-6xl font-bold bg-gradient-to-r from-[#58A6FF] to-[#79C0FF] bg-clip-text text-transparent">
              {data.overallScore}
            </span>
            <span className="text-2xl text-[#8B949E] ml-2">/10</span>
          </div>

          {/* Status Badge */}
          <div className={`px-4 py-2 rounded-lg ${getBgColor(data.status)}`}>
            <div className={`text-sm font-semibold ${getStatusColor(data.status)}`}>
              {data.status}
            </div>
            <div className="text-xs text-[#8B949E] mt-1">
              {data.trend.direction === 'up' && `↑ +${data.trend.value}`}
              {data.trend.direction === 'down' && `↓ -${data.trend.value}`}
              {data.trend.direction === 'stable' && '→ stable'}
            </div>
          </div>
        </div>

        {/* Right Side Info */}
        <div className="mt-6 lg:mt-0 grid grid-cols-2 gap-6">
          <div>
            <div className="text-xs text-[#8B949E] uppercase font-semibold">
              Last Updated
            </div>
            <div className="text-sm text-[#C9D1D9] mt-1">
              {new Date(data.lastUpdated).toLocaleTimeString()}
            </div>
          </div>
          <div>
            <div className="text-xs text-[#8B949E] uppercase font-semibold">
              30-Day Projection
            </div>
            <div className="text-sm text-[#79C0FF] font-semibold mt-1">
              {data.projection}/10
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
