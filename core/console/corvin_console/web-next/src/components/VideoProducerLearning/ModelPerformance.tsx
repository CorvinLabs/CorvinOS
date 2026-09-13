/**
 * Model Performance Component for Video Producer Learning (Phase 4b)
 *
 * Displays model selection statistics with win rates by duration.
 * Integrates with /v1/console/video/learning/models endpoint.
 */

import React, { useState, useEffect } from 'react';

interface ModelStats {
  win_rate: number;
  average_rating: number;
  attempts: number;
  wins: number;
}

interface DurationStats {
  selected_model: string;
  models: Record<string, ModelStats>;
}

interface ModelData {
  total_decisions: number;
  exploration_rate: number;
  by_duration: Record<string, DurationStats>;
}

const MODELS = ['gpt-4', 'claude-opus', 'claude-sonnet'] as const;
const MODEL_COLORS: Record<string, string> = {
  'gpt-4': 'bg-purple-100 text-purple-800 border-purple-300',
  'claude-opus': 'bg-blue-100 text-blue-800 border-blue-300',
  'claude-sonnet': 'bg-green-100 text-green-800 border-green-300',
};

export const ModelPerformance: React.FC = () => {
  const [modelData, setModelData] = useState<ModelData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await fetch('/v1/console/video/learning/models');
        if (!response.ok) throw new Error('Failed to fetch model stats');
        const data = await response.json();
        setModelData(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div className="p-4 text-gray-500">Loading model stats...</div>;
  if (error) return <div className="p-4 text-red-600">Error: {error}</div>;
  if (!modelData) return <div className="p-4 text-gray-500">No model data</div>;

  return (
    <div className="model-performance p-4 space-y-6">
      <h3 className="text-lg font-semibold">Model Performance & Selection</h3>

      {/* Overview Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="p-4 bg-gradient-to-br from-blue-50 to-blue-100 rounded-lg border border-blue-200">
          <p className="text-sm text-blue-600">Total Decisions</p>
          <p className="text-3xl font-bold text-blue-900">{modelData.total_decisions}</p>
        </div>
        <div className="p-4 bg-gradient-to-br from-orange-50 to-orange-100 rounded-lg border border-orange-200">
          <p className="text-sm text-orange-600">Exploration Rate</p>
          <p className="text-3xl font-bold text-orange-900">
            {(modelData.exploration_rate * 100).toFixed(0)}%
          </p>
        </div>
        <div className="p-4 bg-gradient-to-br from-green-50 to-green-100 rounded-lg border border-green-200">
          <p className="text-sm text-green-600">Models Evaluated</p>
          <p className="text-3xl font-bold text-green-900">{MODELS.length}</p>
        </div>
      </div>

      {/* Per-Duration Performance */}
      <div className="space-y-4">
        {Object.entries(modelData.by_duration).map(([duration, stats]) => (
          <div key={duration} className="border rounded-lg p-4">
            <div className="flex items-center justify-between mb-4">
              <h4 className="font-semibold text-gray-900">{duration} Videos</h4>
              <span className={`px-3 py-1 rounded font-semibold text-sm ${MODEL_COLORS[stats.selected_model] || 'bg-gray-100'} border`}>
                Selected: {stats.selected_model}
              </span>
            </div>

            {/* Model Comparison */}
            <div className="space-y-3">
              {MODELS.map((model) => {
                const modelStats = stats.models[model];
                if (!modelStats) return null;

                return (
                  <div key={model} className={`p-3 rounded border ${MODEL_COLORS[model]}`}>
                    <div className="flex items-start justify-between mb-2">
                      <div>
                        <p className="font-semibold">{model}</p>
                        <p className="text-sm opacity-75">
                          {modelStats.wins} wins / {modelStats.attempts} attempts
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-2xl font-bold">
                          {(modelStats.win_rate * 100).toFixed(0)}%
                        </p>
                        <p className="text-xs opacity-75">
                          avg {(modelStats.average_rating * 100).toFixed(0)}%
                        </p>
                      </div>
                    </div>

                    {/* Win Rate Bar */}
                    <div className="w-full bg-white rounded-full h-2 opacity-50">
                      <div
                        className="h-2 rounded-full bg-current transition-all"
                        style={{ width: `${modelStats.win_rate * 100}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Algorithm Info */}
      <div className="p-4 bg-amber-50 rounded-lg border border-amber-200">
        <h4 className="font-semibold text-amber-900 mb-2">Algorithm</h4>
        <p className="text-sm text-amber-800 mb-2">
          Epsilon-Greedy Multi-Armed Bandit (ε=0.1)
        </p>
        <ul className="text-sm text-amber-700 space-y-1">
          <li>• 10% exploration (random model selection)</li>
          <li>• 90% exploitation (best model by win rate)</li>
          <li>• Per-duration grouping (different models for different video lengths)</li>
          <li>• Switches when new model is 15% better than current</li>
        </ul>
      </div>
    </div>
  );
};

export default ModelPerformance;
