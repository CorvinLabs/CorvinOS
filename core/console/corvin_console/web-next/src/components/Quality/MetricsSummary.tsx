/**
 * MetricsSummary — Quality score display with DoD/Hallucin breakdown
 */

import React from "react";
import { QualityMetrics } from "./types";

interface Props {
  metrics: QualityMetrics;
}

export default function MetricsSummary({ metrics }: Props) {
  const scoreColor = getScoreColor(metrics.quality_score);
  const statusBadge = getStatusBadge(metrics.status);

  return (
    <div className="bg-white border rounded-lg p-6 shadow-sm">
      <div className="flex justify-between items-start mb-4">
        <div>
          <h3 className="text-sm text-gray-600 mb-1">Unified Quality Score</h3>
          <div className={`text-4xl font-bold ${scoreColor}`}>
            {(metrics.quality_score * 100).toFixed(1)}%
          </div>
        </div>
        <span
          className={`px-3 py-1 rounded text-sm font-medium ${statusBadge}`}
        >
          {metrics.status}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4 mt-6">
        <div>
          <p className="text-xs text-gray-600 mb-1">DoD Score</p>
          <div className="text-2xl font-semibold">
            {(metrics.dod_score * 100).toFixed(0)}%
          </div>
          <div className="h-2 bg-gray-200 rounded mt-2">
            <div
              className="h-full bg-blue-500 rounded"
              style={{ width: `${metrics.dod_score * 100}%` }}
            />
          </div>
        </div>
        <div>
          <p className="text-xs text-gray-600 mb-1">Hallucination Score</p>
          <div className="text-2xl font-semibold">
            {(metrics.hallucin_score * 100).toFixed(0)}%
          </div>
          <div className="h-2 bg-gray-200 rounded mt-2">
            <div
              className="h-full bg-green-500 rounded"
              style={{ width: `${metrics.hallucin_score * 100}%` }}
            />
          </div>
        </div>
      </div>

      <div className="mt-6 text-xs text-gray-500">
        <p>Task: {metrics.task_type} • Size: {metrics.task_size}</p>
        <p>Iteration: {metrics.iteration} • Spec v{metrics.spec_version}</p>
        <p>Last updated: {new Date(metrics.last_updated).toLocaleTimeString()}</p>
      </div>
    </div>
  );
}

function getScoreColor(score: number): string {
  if (score >= 0.9) return "text-green-600";
  if (score >= 0.8) return "text-blue-600";
  if (score >= 0.7) return "text-yellow-600";
  return "text-red-600";
}

function getStatusBadge(status: string): string {
  const base = "px-3 py-1 rounded text-sm font-medium";
  if (status === "converged") return `${base} bg-green-100 text-green-800`;
  if (status === "in_progress") return `${base} bg-blue-100 text-blue-800`;
  return `${base} bg-gray-100 text-gray-800`;
}
