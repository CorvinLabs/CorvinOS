/**
 * ConvergenceChart — Recharts LineChart + AreaChart for quality trajectory
 * (ADR-0733, ADR-0731)
 */

import React from "react";
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { ConvergenceHistoryPoint } from "./types";

interface Props {
  data: ConvergenceHistoryPoint[];
}

export default function ConvergenceChart({ data }: Props) {
  if (!data || data.length === 0) {
    return <div className="text-gray-500">No convergence data available</div>;
  }

  return (
    <div className="bg-white border rounded-lg p-6 shadow-sm space-y-6">
      {/* Unified Quality Score over iterations */}
      <div>
        <h4 className="text-sm font-semibold mb-4">Quality Score (iterations)</h4>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="iteration" />
            <YAxis domain={[0, 1]} />
            <Tooltip
              formatter={(val) => [(val as number).toFixed(3), "Score"]}
              labelFormatter={(label) => `Iteration ${label}`}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey="quality_score"
              stroke="#2563eb"
              name="Quality"
              dot={true}
              isAnimationActive={true}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* DoD vs Hallucination breakdown */}
      <div>
        <h4 className="text-sm font-semibold mb-4">DoD vs Hallucination</h4>
        <ResponsiveContainer width="100%" height={300}>
          <AreaChart data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="iteration" />
            <YAxis domain={[0, 1]} />
            <Tooltip
              formatter={(val) => [(val as number).toFixed(3), "Score"]}
              labelFormatter={(label) => `Iteration ${label}`}
            />
            <Legend />
            <Area
              type="monotone"
              dataKey="dod_score"
              stackId="1"
              stroke="#3b82f6"
              fill="#bfdbfe"
              name="DoD"
            />
            <Area
              type="monotone"
              dataKey="hallucin_score"
              stackId="1"
              stroke="#10b981"
              fill="#d1fae5"
              name="Hallucination"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Loss gradient (1 - quality_score) */}
      <div>
        <h4 className="text-sm font-semibold mb-4">Loss Gradient</h4>
        <ResponsiveContainer width="100%" height={250}>
          <LineChart data={data.map((p) => ({ ...p, loss_gradient: 1 - p.quality_score }))}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="iteration" />
            <YAxis domain={[0, 1]} />
            <Tooltip
              formatter={(val) => [(val as number).toFixed(3), "Loss"]}
              labelFormatter={(label) => `Iteration ${label}`}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey="loss_gradient"
              stroke="#ef4444"
              name="Loss"
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 text-sm pt-4 border-t">
        <div>
          <p className="text-gray-600">Total Iterations</p>
          <p className="font-semibold text-lg">{data.length}</p>
        </div>
        <div>
          <p className="text-gray-600">Initial Quality</p>
          <p className="font-semibold text-lg">
            {(data[0]?.quality_score * 100).toFixed(0)}%
          </p>
        </div>
        <div>
          <p className="text-gray-600">Final Quality</p>
          <p className="font-semibold text-lg">
            {(data[data.length - 1]?.quality_score * 100).toFixed(0)}%
          </p>
        </div>
        <div>
          <p className="text-gray-600">Improvement</p>
          <p className="font-semibold text-lg">
            +{((data[data.length - 1]?.quality_score - data[0]?.quality_score) * 100).toFixed(0)}%
          </p>
        </div>
      </div>
    </div>
  );
}
