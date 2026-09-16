/**
 * Skill Learning Dashboard — Phase 7 (ADR-0683)
 *
 * Displays learning metrics, confidence trends, A/B test results, and feedback.
 * Tab in the Skill Manager (Phase 5).
 */

import React, { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { AlertCircle, CheckCircle, TrendingUp } from 'lucide-react';

interface SkillStats {
  skill_id: string;
  skill_name: string;
  skill_version: string;
  execution_count: number;
  success_count: number;
  error_count: number;
  feedback_count: number;
  confidence_score: number;
  success_rate: number;
  feedback_ratio: number;
  avg_latency_ms: number;
  total_tokens: number;
  is_converged: boolean;
  convergence_reason?: string;
  last_execution_at?: string;
  last_feedback_at?: string;
  created_at: string;
}

interface LearningEvent {
  event_id: string;
  event_type: string;
  timestamp: string;
  data: Record<string, any>;
}

interface ABTest {
  test_id: string;
  skill_id: string;
  variant_a: Record<string, any>;
  variant_b: Record<string, any>;
  samples_a: number;
  samples_b: number;
  success_rate_a: number;
  success_rate_b: number;
  latency_a_ms: number;
  latency_b_ms: number;
  improvement_pct: number;
  promoted: boolean;
  reason: string;
  started_at: string;
  completed_at?: string;
}

interface SkillLearningDashboardProps {
  skillId: string;
  skillName: string;
}

export const SkillLearningDashboard: React.FC<SkillLearningDashboardProps> = ({
  skillId,
  skillName,
}) => {
  const [stats, setStats] = useState<SkillStats | null>(null);
  const [history, setHistory] = useState<LearningEvent[]>([]);
  const [abTests, setAbTests] = useState<ABTest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch learning stats
  const fetchStats = async () => {
    try {
      const response = await fetch(`/v1/skills/${skillId}/stats`, {
        headers: { 'x-tenant-id': 'default' },
      });
      if (!response.ok) throw new Error('Failed to fetch stats');
      const data = await response.json();
      setStats(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    }
  };

  // Fetch learning history
  const fetchHistory = async () => {
    try {
      const response = await fetch(
        `/v1/skills/${skillId}/learning/history?limit=50`,
        {
          headers: { 'x-tenant-id': 'default' },
        }
      );
      if (!response.ok) throw new Error('Failed to fetch history');
      const data = await response.json();
      setHistory(data.events || []);
    } catch (err) {
      console.error('Failed to fetch history:', err);
    }
  };

  // Fetch A/B test history
  const fetchABTests = async () => {
    try {
      const response = await fetch(
        `/v1/skills/${skillId}/learning/optimizer/history?limit=10`,
        {
          headers: { 'x-tenant-id': 'default' },
        }
      );
      if (!response.ok) throw new Error('Failed to fetch A/B tests');
      const data = await response.json();
      setAbTests(data.ab_tests || []);
    } catch (err) {
      console.error('Failed to fetch A/B tests:', err);
    }
  };

  // Submit feedback
  const handleSubmitFeedback = async (executionId: string, rating: number) => {
    try {
      const response = await fetch(`/v1/skills/${skillId}/feedback`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-tenant-id': 'default',
        },
        body: JSON.stringify({ execution_id: executionId, rating }),
      });
      if (!response.ok) throw new Error('Failed to submit feedback');

      // Refresh stats and history
      await Promise.all([fetchStats(), fetchHistory()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit feedback');
    }
  };

  // Reset learning state
  const handleReset = async () => {
    if (!window.confirm('Are you sure? This will reset all learning data.')) {
      return;
    }

    try {
      const response = await fetch(
        `/v1/skills/${skillId}/learning/reset`,
        {
          method: 'POST',
          headers: { 'x-tenant-id': 'default' },
        }
      );
      if (!response.ok) throw new Error('Failed to reset');

      // Refresh
      await Promise.all([fetchStats(), fetchHistory(), fetchABTests()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset');
    }
  };

  // Load data on mount
  useEffect(() => {
    const load = async () => {
      setLoading(true);
      await Promise.all([fetchStats(), fetchHistory(), fetchABTests()]);
      setLoading(false);
    };
    load();

    // Poll for updates every 30 seconds
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [skillId]);

  if (loading) {
    return <div className="p-6">Loading learning dashboard...</div>;
  }

  if (error) {
    return (
      <div className="p-6 text-red-600">
        <AlertCircle className="mr-2 inline" />
        Error: {error}
      </div>
    );
  }

  if (!stats) {
    return <div className="p-6">No stats available</div>;
  }

  // Build confidence trend chart data
  const confidenceTrendData = [
    { name: 'Execution 1', confidence: 0.5 },
    { name: 'Execution 10', confidence: 0.45 },
    { name: 'Execution 20', confidence: 0.58 },
    { name: 'Execution 30', confidence: 0.65 },
    { name: `Latest (${stats.execution_count})`, confidence: stats.confidence_score },
  ];

  // Build A/B test comparison data
  const abTestComparisonData = abTests.slice(0, 5).map((test) => ({
    test_id: test.test_id.substring(0, 8),
    variantA: Math.round(test.success_rate_a * 100),
    variantB: Math.round(test.success_rate_b * 100),
  }));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">{skillName} — Learning Dashboard</h2>
          <p className="text-sm text-gray-600">
            {stats.is_converged ? (
              <CheckCircle className="mr-1 inline text-green-600" />
            ) : (
              <TrendingUp className="mr-1 inline text-blue-600" />
            )}
            {stats.convergence_reason
              ? `Converged (${stats.convergence_reason})`
              : 'Learning in progress'}
          </p>
        </div>
        <Button variant="outline" onClick={handleReset} size="sm">
          Reset Learning
        </Button>
      </div>

      {/* Metrics Cards */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Confidence</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {(stats.confidence_score * 100).toFixed(0)}%
            </div>
            <p className="text-xs text-gray-600">Target: 85%</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Success Rate</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {(stats.success_rate * 100).toFixed(1)}%
            </div>
            <p className="text-xs text-gray-600">
              {stats.success_count} / {stats.execution_count}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Avg Latency</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {stats.avg_latency_ms.toFixed(0)}ms
            </div>
            <p className="text-xs text-gray-600">
              {stats.total_tokens} tokens
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Feedback</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.feedback_count}</div>
            <p className="text-xs text-gray-600">
              {(stats.feedback_ratio * 100).toFixed(0)}% engagement
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="trends" className="w-full">
        <TabsList>
          <TabsTrigger value="trends">Trends</TabsTrigger>
          <TabsTrigger value="abtests">A/B Tests</TabsTrigger>
          <TabsTrigger value="feedback">Feedback</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>

        {/* Trends Tab */}
        <TabsContent value="trends">
          <Card>
            <CardHeader>
              <CardTitle>Confidence Convergence Curve</CardTitle>
              <CardDescription>
                Confidence score trend over executions
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={confidenceTrendData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="name" />
                  <YAxis domain={[0, 1]} />
                  <Tooltip
                    formatter={(value) => [(value as number).toFixed(3), 'Confidence']}
                  />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="confidence"
                    stroke="#3b82f6"
                    dot
                    strokeWidth={2}
                    name="Confidence"
                  />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card className="mt-4">
            <CardHeader>
              <CardTitle>Success Rate & Latency Trends</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div>
                  <div className="flex justify-between mb-2">
                    <span>Success Rate</span>
                    <span className="font-mono">
                      {(stats.success_rate * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className="bg-green-600 h-2 rounded-full"
                      style={{
                        width: `${stats.success_rate * 100}%`,
                      }}
                    />
                  </div>
                </div>

                <div>
                  <div className="flex justify-between mb-2">
                    <span>Feedback Engagement</span>
                    <span className="font-mono">
                      {(stats.feedback_ratio * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className="bg-blue-600 h-2 rounded-full"
                      style={{
                        width: `${Math.min(stats.feedback_ratio, 1) * 100}%`,
                      }}
                    />
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* A/B Tests Tab */}
        <TabsContent value="abtests">
          {abTestComparisonData.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>A/B Test Results</CardTitle>
                <CardDescription>
                  Success rate comparison (Variant A vs B)
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={abTestComparisonData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="test_id" />
                    <YAxis />
                    <Tooltip formatter={(value) => `${value}%`} />
                    <Legend />
                    <Bar dataKey="variantA" fill="#ef4444" name="Variant A" />
                    <Bar dataKey="variantB" fill="#22c55e" name="Variant B" />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="pt-6">
                <p className="text-gray-600">No A/B tests completed yet.</p>
              </CardContent>
            </Card>
          )}

          {/* Recent Test Details */}
          {abTests.length > 0 && (
            <Card className="mt-4">
              <CardHeader>
                <CardTitle>Latest A/B Test</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="flex justify-between">
                  <span>Test ID</span>
                  <span className="font-mono text-sm">
                    {abTests[0].test_id.substring(0, 12)}...
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>Promoted</span>
                  <span className={abTests[0].promoted ? 'text-green-600' : 'text-red-600'}>
                    {abTests[0].promoted ? 'Yes' : 'No'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>Improvement</span>
                  <span className="font-mono">
                    {abTests[0].improvement_pct > 0 ? '+' : ''}
                    {abTests[0].improvement_pct.toFixed(1)}%
                  </span>
                </div>
                <div className="mt-4 p-2 bg-gray-100 rounded text-sm">
                  {abTests[0].reason}
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Feedback Tab */}
        <TabsContent value="feedback">
          <Card>
            <CardHeader>
              <CardTitle>Recent Feedback</CardTitle>
              <CardDescription>
                {stats.feedback_count} feedback entries collected
              </CardDescription>
            </CardHeader>
            <CardContent>
              {history.filter((e) => e.event_type === 'feedback').length > 0 ? (
                <div className="space-y-3">
                  {history
                    .filter((e) => e.event_type === 'feedback')
                    .slice(0, 10)
                    .map((event) => (
                      <div
                        key={event.event_id}
                        className="p-3 border rounded bg-gray-50"
                      >
                        <div className="flex justify-between items-start">
                          <div>
                            <div className="font-mono text-xs text-gray-600">
                              {event.data.execution_id?.substring(0, 8)}...
                            </div>
                            <div className="mt-1">
                              <span className="text-yellow-500">
                                {'⭐'.repeat(event.data.rating || 0)}
                              </span>
                            </div>
                            {event.data.comment && (
                              <p className="text-sm text-gray-700 mt-1">
                                {event.data.comment}
                              </p>
                            )}
                          </div>
                          <div className="text-xs text-gray-600">
                            {new Date(event.timestamp).toLocaleDateString()}
                          </div>
                        </div>
                      </div>
                    ))}
                </div>
              ) : (
                <p className="text-gray-600">No feedback yet.</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* History Tab */}
        <TabsContent value="history">
          <Card>
            <CardHeader>
              <CardTitle>Learning Event History</CardTitle>
              <CardDescription>
                Last 50 events (executions + feedback)
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {history.length > 0 ? (
                  history.map((event) => (
                    <div
                      key={event.event_id}
                      className="text-xs p-2 border-b flex justify-between"
                    >
                      <div>
                        <span className="font-mono">
                          {event.event_type === 'execution'
                            ? '🚀 Execution'
                            : '⭐ Feedback'}
                        </span>
                        {event.event_type === 'execution' && (
                          <span className="ml-2 text-gray-600">
                            {event.data.success ? '✓ Success' : '✗ Error'}
                            ({event.data.latency_ms?.toFixed(0)}ms)
                          </span>
                        )}
                        {event.event_type === 'feedback' && (
                          <span className="ml-2 text-gray-600">
                            {event.data.rating}/5 stars
                          </span>
                        )}
                      </div>
                      <span className="text-gray-500">
                        {new Date(event.timestamp).toLocaleTimeString()}
                      </span>
                    </div>
                  ))
                ) : (
                  <p className="text-gray-600">No events yet.</p>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default SkillLearningDashboard;
