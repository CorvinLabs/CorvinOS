/**
 * ErrorGauge Component
 *
 * Displays error rate as a gauge/progress indicator.
 * Green: <0.5%, Yellow: 0.5-1%, Red: >1%
 */

import React from 'react';

interface ErrorGaugeProps {
  errorRate: number; // percentage (0-100)
}

export function ErrorGauge({ errorRate }: ErrorGaugeProps) {
  // Determine color based on rate
  let color: string;
  let status: string;
  if (errorRate < 0.5) {
    color = '#3FB950'; // Green
    status = 'Excellent';
  } else if (errorRate < 1.0) {
    color = '#D29922'; // Yellow
    status = 'Acceptable';
  } else {
    color = '#F85149'; // Red
    status = 'High Risk';
  }

  // Clamp percentage for gauge visualization (0-2% scale for better granularity)
  const gaugePercent = Math.min((errorRate / 2) * 100, 100);

  return (
    <div className="flex flex-col gap-4">
      {/* Gauge visualization */}
      <div className="space-y-2">
        <div className="text-3xl font-bold" style={{ color }}>
          {errorRate.toFixed(2)}%
        </div>
        <div className="text-xs text-[#8B949E]">{status}</div>
      </div>

      {/* Gauge bar */}
      <div className="w-full bg-[#0D1117] rounded-full h-3 overflow-hidden">
        <div
          className="h-full transition-all duration-300"
          style={{
            width: `${gaugePercent}%`,
            backgroundColor: color,
          }}
        ></div>
      </div>

      {/* Thresholds */}
      <div className="grid grid-cols-3 gap-1 text-xs">
        <div className="text-center">
          <div className="text-[#8B949E]">OK</div>
          <div className="text-[#3FB950]">&lt;0.5%</div>
        </div>
        <div className="text-center">
          <div className="text-[#8B949E]">WARN</div>
          <div className="text-[#D29922]">0.5-1%</div>
        </div>
        <div className="text-center">
          <div className="text-[#8B949E]">ALERT</div>
          <div className="text-[#F85149]">&gt;1%</div>
        </div>
      </div>

      {/* SLO info */}
      <div className="text-xs text-[#8B949E] border-t border-[#30363D] pt-2 mt-2">
        Week 5 Target: &lt;1% error rate (Phase B cleanup)
      </div>
    </div>
  );
}
