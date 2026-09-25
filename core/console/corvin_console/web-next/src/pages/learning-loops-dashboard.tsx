/**
 * Learning Loops Dashboard — Console Panel
 * ADR-0906 Feature 1: UI wiring for learning loop manifest + health
 */

import React, { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';

interface LearningLoop {
  loop_id: string;
  plugin_id: string;
  description: string;
  event_source: string;
  feedback_types: string[];
  aggregation: string;
  health_threshold?: number;
  dormancy_alert_hours?: number;
  owner_skill?: string;
  last_event_ts?: string;
  event_count_7d: number;
  health_score?: number;
  status: 'active' | 'dormant' | 'degrading' | 'stale' | 'unknown';
}

export function LearningLoopsDashboard() {
  const [loops, setLoops] = useState<LearningLoop[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchLearningLoops();
  }, []);

  const fetchLearningLoops = async () => {
    try {
      setLoading(true);
      const response = await fetch('/v1/console/learning/loops');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setLoops(data.learning_loops || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setLoops([]);
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (status: LearningLoop['status']) => {
    switch (status) {
      case 'active':
        return 'bg-green-100 text-green-800';
      case 'dormant':
        return 'bg-yellow-100 text-yellow-800';
      case 'degrading':
        return 'bg-red-100 text-red-800';
      case 'stale':
        return 'bg-gray-100 text-gray-800';
      default:
        return 'bg-blue-100 text-blue-800';
    }
  };

  const getStatusEmoji = (status: LearningLoop['status']) => {
    switch (status) {
      case 'active':
        return '🟢';
      case 'dormant':
        return '🟡';
      case 'degrading':
        return '🔴';
      case 'stale':
        return '⚫';
      default:
        return '❓';
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold">Learning Loops</h1>
        <p className="text-gray-600 mt-2">
          Monitor learning loop health and event activity
        </p>
      </div>

      {/* Refresh Button */}
      <button
        onClick={fetchLearningLoops}
        disabled={loading}
        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? 'Loading...' : 'Refresh'}
      </button>

      {/* Error Alert */}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Loading State */}
      {loading && <p className="text-gray-500">Loading learning loops...</p>}

      {/* Empty State */}
      {!loading && loops.length === 0 && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-gray-600">No learning loops registered yet.</p>
          </CardContent>
        </Card>
      )}

      {/* Loops Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {loops.map((loop) => (
          <Card key={loop.loop_id}>
            <CardHeader>
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <CardTitle className="text-lg">
                    {loop.loop_id.split(':')[1] || loop.loop_id}
                  </CardTitle>
                  <CardDescription>{loop.description}</CardDescription>
                </div>
                <Badge className={getStatusColor(loop.status)}>
                  {getStatusEmoji(loop.status)} {loop.status}
                </Badge>
              </div>
            </CardHeader>

            <CardContent className="space-y-4">
              {/* Health Score */}
              {loop.health_score !== null && loop.health_score !== undefined && (
                <div>
                  <p className="text-sm font-semibold">Health Score</p>
                  <div className="flex items-center gap-2 mt-1">
                    <div className="flex-1 bg-gray-200 rounded h-2">
                      <div
                        className="bg-blue-600 h-2 rounded"
                        style={{
                          width: `${Math.min(100, loop.health_score * 100)}%`,
                        }}
                      ></div>
                    </div>
                    <span className="text-sm font-mono">
                      {(loop.health_score * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              )}

              {/* Event Count */}
              <div>
                <p className="text-sm font-semibold">Events (7 days)</p>
                <p className="text-2xl font-bold">{loop.event_count_7d}</p>
              </div>

              {/* Event Source */}
              <div>
                <p className="text-sm font-semibold">Event Source</p>
                <p className="text-xs text-gray-600 font-mono">
                  {loop.event_source}
                </p>
              </div>

              {/* Feedback Types */}
              <div>
                <p className="text-sm font-semibold">Feedback Types</p>
                <div className="flex flex-wrap gap-1 mt-1">
                  {loop.feedback_types.map((type) => (
                    <Badge key={type} variant="outline" className="text-xs">
                      {type}
                    </Badge>
                  ))}
                </div>
              </div>

              {/* Owner Skill */}
              {loop.owner_skill && (
                <div>
                  <p className="text-sm font-semibold">Owner Skill</p>
                  <p className="text-xs text-gray-600 font-mono">
                    {loop.owner_skill}
                  </p>
                </div>
              )}

              {/* Last Event */}
              {loop.last_event_ts && (
                <div>
                  <p className="text-sm font-semibold">Last Event</p>
                  <p className="text-xs text-gray-600">
                    {new Date(loop.last_event_ts).toLocaleString()}
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

export default LearningLoopsDashboard;
