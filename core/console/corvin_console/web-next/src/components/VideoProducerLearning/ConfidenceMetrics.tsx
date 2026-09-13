/**
 * Confidence Metrics Component for Video Producer Learning (Phase 4b)
 *
 * Displays per-worker confidence scores with trend visualization.
 * Integrates with /v1/console/video/learning/confidence endpoint.
 */

import React, { useState, useEffect } from 'react';

export interface WorkerConfidenceData {
  overall_score: number;
  is_converged: boolean;
  convergence_rate: number;
  metrics: Record<string, MetricData>;
}

interface MetricData {
  confidence: number;
  samples: number;
  variance: number;
  last_updated: string;
}

export const ConfidenceMetrics: React.FC = () => {
  const [metrics, setMetrics] = useState<Record<string, WorkerConfidenceData>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const response = await fetch('/v1/console/video/learning/confidence');
        if (!response.ok) throw new Error('Failed to fetch metrics');
        const data = await response.json();
        setMetrics(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  const getScoreColor = (score: number): string => {
    if (score >= 0.85) return 'bg-green-100 text-green-800';
    if (score >= 0.7) return 'bg-yellow-100 text-yellow-800';
    return 'bg-red-100 text-red-800';
  };

  const getConvergenceIcon = (converged: boolean): string => {
    return converged ? '✓' : '○';
  };

  if (loading) return <div className="p-4 text-gray-500">Loading metrics...</div>;
  if (error) return <div className="p-4 text-red-600">Error: {error}</div>;

  return (
    <div className="confidence-metrics p-4 space-y-6">
      <h3 className="text-lg font-semibold">Worker Confidence Scores</h3>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {Object.entries(metrics).map(([workerId, data]) => (
          <div
            key={workerId}
            className="p-4 border border-gray-200 rounded-lg hover:shadow-md transition-shadow"
          >
            {/* Worker Header */}
            <div className="flex items-start justify-between mb-3">
              <div>
                <h4 className="font-semibold text-gray-900">{workerId}</h4>
                <p className="text-xs text-gray-500">
                  {data.is_converged ? '✓ Converged' : '○ Learning'}
                </p>
              </div>
              <div className={`text-2xl font-bold px-3 py-1 rounded ${getScoreColor(data.overall_score)}`}>
                {(data.overall_score * 100).toFixed(0)}%
              </div>
            </div>

            {/* Metrics */}
            <div className="space-y-2">
              {Object.entries(data.metrics).map(([metricName, metric]) => (
                <div key={metricName} className="text-sm">
                  <div className="flex justify-between mb-1">
                    <span className="text-gray-600">{metricName}</span>
                    <span className="font-semibold">{(metric.confidence * 100).toFixed(0)}%</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className="bg-blue-500 h-2 rounded-full transition-all"
                      style={{ width: `${metric.confidence * 100}%` }}
                    />
                  </div>
                  <p className="text-xs text-gray-500 mt-1">
                    {metric.samples} samples • σ={metric.variance.toFixed(2)}
                  </p>
                </div>
              ))}
            </div>

            {/* Convergence Rate */}
            <div className="mt-4 pt-3 border-t border-gray-200">
              <p className="text-xs text-gray-600 mb-1">Convergence Rate</p>
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div
                  className="bg-green-500 h-2 rounded-full transition-all"
                  style={{ width: `${data.convergence_rate * 100}%` }}
                />
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Summary Stats */}
      <div className="p-4 bg-blue-50 rounded-lg border border-blue-200">
        <h4 className="font-semibold text-blue-900 mb-2">Summary</h4>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <p className="text-blue-600">Workers Tracked</p>
            <p className="text-xl font-bold text-blue-900">{Object.keys(metrics).length}</p>
          </div>
          <div>
            <p className="text-blue-600">Converged</p>
            <p className="text-xl font-bold text-blue-900">
              {Object.values(metrics).filter((m) => m.is_converged).length}
            </p>
          </div>
          <div>
            <p className="text-blue-600">Avg Confidence</p>
            <p className="text-xl font-bold text-blue-900">
              {(
                Object.values(metrics).reduce((sum, m) => sum + m.overall_score, 0) /
                  Object.keys(metrics).length || 0
              ).toFixed(1)}
            </p>
          </div>
          <div>
            <p className="text-blue-600">Metrics Tracked</p>
            <p className="text-xl font-bold text-blue-900">
              {Object.values(metrics).reduce((sum, m) => sum + Object.keys(m.metrics).length, 0)}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ConfidenceMetrics;
