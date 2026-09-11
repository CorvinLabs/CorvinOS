/**
 * Learning Dashboard Panel: DataHub Creator Phase 4
 *
 * Displays:
 * - Skill generation history (timeline)
 * - Weight update history (line chart)
 * - User feedback impact (bar chart)
 * - Convergence metrics (gauge + status)
 * - Real-time updates (5s polling)
 */

import React, { useState, useEffect } from 'react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';

interface SkillRecord {
  skill_id: string;
  skill_name: string;
  timestamp: string;
  loss_before: number;
  loss_after: number;
  improvement_pct: number;
  phase_count: number;
  source?: string;
}

interface WeightUpdate {
  timestamp: string;
  source_id: string;
  weight_before: number;
  weight_after: number;
  change_pct: number;
  reason: string;
}

interface FeedbackSignal {
  timestamp: string;
  skill_id: string;
  signal: string; // "positive" | "negative" | "neutral"
  impact_on_loss: number;
}

interface ConvergenceStatus {
  is_converged: boolean;
  confidence: number;
  samples_processed: number;
  last_update: string;
  estimated_weeks_to_stable: number;
}

const COLORS = {
  positive: '#10b981',
  negative: '#ef4444',
  neutral: '#6b7280',
  primary: '#3b82f6',
  secondary: '#8b5cf6',
};

export const LearningDashboard: React.FC = () => {
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [weights, setWeights] = useState<WeightUpdate[]>([]);
  const [feedback, setFeedback] = useState<FeedbackSignal[]>([]);
  const [convergence, setConvergence] = useState<ConvergenceStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'overview' | 'skills' | 'weights' | 'feedback'>('overview');
  const [lastUpdate, setLastUpdate] = useState<string>('');

  // Fetch data on mount and periodic refresh
  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000); // Refresh every 5s
    return () => clearInterval(interval);
  }, []);

  const fetchData = async () => {
    try {
      const [skillsRes, weightsRes, feedbackRes, convergenceRes] = await Promise.all([
        fetch('/api/v1/learning/skills?limit=50'),
        fetch('/api/v1/learning/weights?limit=100'),
        fetch('/api/v1/learning/feedback?limit=100'),
        fetch('/api/v1/learning/convergence'),
      ]);

      if (skillsRes.ok) {
        const data = await skillsRes.json();
        setSkills(data.data || []);
      }
      if (weightsRes.ok) {
        const data = await weightsRes.json();
        setWeights(data.data || []);
      }
      if (feedbackRes.ok) {
        const data = await feedbackRes.json();
        setFeedback(data.data || []);
      }
      if (convergenceRes.ok) {
        const data = await convergenceRes.json();
        setConvergence(data);
      }

      setLastUpdate(new Date().toLocaleTimeString());
    } catch (error) {
      console.error('Failed to fetch learning data:', error);
    } finally {
      setLoading(false);
    }
  };

  // Compute statistics
  const totalSkillsGenerated = skills.length;
  const avgImprovement = skills.length > 0
    ? (skills.reduce((sum, s) => sum + s.improvement_pct, 0) / skills.length).toFixed(1)
    : 0;

  const feedbackCounts = {
    positive: feedback.filter(f => f.signal === 'positive').length,
    negative: feedback.filter(f => f.signal === 'negative').length,
    neutral: feedback.filter(f => f.signal === 'neutral').length,
  };

  const convergencePercent = convergence ? Math.round(convergence.confidence * 100) : 0;

  if (loading && skills.length === 0) {
    return (
      <div className="p-6 bg-slate-50 dark:bg-slate-900 rounded-lg text-center">
        <div className="animate-pulse">Loading learning dashboard...</div>
      </div>
    );
  }

  return (
    <div className="p-6 bg-gradient-to-br from-slate-50 to-slate-100 dark:from-slate-900 dark:to-slate-800 min-h-screen rounded-lg">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-900 dark:text-white mb-2">
          🧠 Learning Dashboard
        </h1>
        <p className="text-slate-600 dark:text-slate-400">
          DataHub Creator Phase 4 • Last update: {lastUpdate}
        </p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
          <div className="text-slate-600 dark:text-slate-400 text-sm font-semibold mb-2">Skills Generated</div>
          <div className="text-3xl font-bold text-slate-900 dark:text-white">{totalSkillsGenerated}</div>
          <div className="text-xs text-slate-500 dark:text-slate-400 mt-2">in this period</div>
        </div>

        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
          <div className="text-slate-600 dark:text-slate-400 text-sm font-semibold mb-2">Avg Improvement</div>
          <div className="text-3xl font-bold text-green-600">{avgImprovement}%</div>
          <div className="text-xs text-slate-500 dark:text-slate-400 mt-2">loss reduction per skill</div>
        </div>

        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
          <div className="text-slate-600 dark:text-slate-400 text-sm font-semibold mb-2">Feedback Count</div>
          <div className="text-3xl font-bold text-slate-900 dark:text-white">{feedback.length}</div>
          <div className="text-xs text-slate-500 dark:text-slate-400 mt-2">total signals</div>
        </div>

        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
          <div className="text-slate-600 dark:text-slate-400 text-sm font-semibold mb-2">Convergence</div>
          <div className={`text-3xl font-bold ${convergence?.is_converged ? 'text-green-600' : 'text-yellow-600'}`}>
            {convergencePercent}%
          </div>
          <div className="text-xs text-slate-500 dark:text-slate-400 mt-2">
            {convergence?.is_converged ? 'Stable' : `~${convergence?.estimated_weeks_to_stable}w to stable`}
          </div>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="flex gap-2 mb-6 border-b border-slate-200 dark:border-slate-700">
        {['overview', 'skills', 'weights', 'feedback'].map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab as any)}
            className={`px-4 py-2 font-medium text-sm transition-colors ${
              activeTab === tab
                ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-600 dark:border-blue-400'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-300'
            }`}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Feedback Distribution */}
          <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white mb-4">Feedback Distribution</h3>
            {feedback.length > 0 ? (
              <ResponsiveContainer width="100%" height={250}>
                <PieChart>
                  <Pie
                    data={[
                      { name: 'Positive', value: feedbackCounts.positive, color: COLORS.positive },
                      { name: 'Negative', value: feedbackCounts.negative, color: COLORS.negative },
                      { name: 'Neutral', value: feedbackCounts.neutral, color: COLORS.neutral },
                    ]}
                    cx="50%"
                    cy="50%"
                    outerRadius={80}
                    dataKey="value"
                    label
                  >
                    <Cell fill={COLORS.positive} />
                    <Cell fill={COLORS.negative} />
                    <Cell fill={COLORS.neutral} />
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-slate-500 dark:text-slate-400">No feedback data yet</p>
            )}
          </div>

          {/* Convergence Status */}
          <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white mb-4">Convergence Status</h3>
            {convergence && (
              <div className="space-y-4">
                <div>
                  <div className="flex justify-between mb-2">
                    <span className="text-sm font-medium text-slate-700 dark:text-slate-300">Confidence</span>
                    <span className="text-sm font-medium text-slate-900 dark:text-white">{convergencePercent}%</span>
                  </div>
                  <div className="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-2">
                    <div
                      className="bg-blue-600 h-2 rounded-full transition-all"
                      style={{ width: `${convergencePercent}%` }}
                    />
                  </div>
                </div>
                <div className="text-sm text-slate-600 dark:text-slate-400">
                  <p><strong>Status:</strong> {convergence.is_converged ? '✅ Converged' : '🔄 Learning'}</p>
                  <p><strong>Samples:</strong> {convergence.samples_processed}</p>
                  <p><strong>ETA:</strong> ~{convergence.estimated_weeks_to_stable} weeks to stable</p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Skills Tab */}
      {activeTab === 'skills' && (
        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700 overflow-x-auto">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-white mb-4">Skill Generation History</h3>
          {skills.length > 0 ? (
            <table className="w-full text-sm">
              <thead className="bg-slate-50 dark:bg-slate-700">
                <tr>
                  <th className="px-4 py-2 text-left text-slate-700 dark:text-slate-300 font-semibold">Skill</th>
                  <th className="px-4 py-2 text-left text-slate-700 dark:text-slate-300 font-semibold">Time</th>
                  <th className="px-4 py-2 text-center text-slate-700 dark:text-slate-300 font-semibold">Loss Before</th>
                  <th className="px-4 py-2 text-center text-slate-700 dark:text-slate-300 font-semibold">Loss After</th>
                  <th className="px-4 py-2 text-center text-slate-700 dark:text-slate-300 font-semibold">Improvement</th>
                  <th className="px-4 py-2 text-center text-slate-700 dark:text-slate-300 font-semibold">Phases</th>
                </tr>
              </thead>
              <tbody>
                {skills.map((skill, idx) => (
                  <tr key={idx} className="border-t border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50">
                    <td className="px-4 py-3 text-slate-900 dark:text-white font-medium">{skill.skill_name}</td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-400">{new Date(skill.timestamp).toLocaleString()}</td>
                    <td className="px-4 py-3 text-center text-slate-900 dark:text-white">{skill.loss_before.toFixed(2)}</td>
                    <td className="px-4 py-3 text-center text-slate-900 dark:text-white">{skill.loss_after.toFixed(2)}</td>
                    <td className="px-4 py-3 text-center">
                      <span className={`font-semibold ${skill.improvement_pct > 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {skill.improvement_pct > 0 ? '+' : ''}{skill.improvement_pct.toFixed(1)}%
                      </span>
                    </td>
                    <td className="px-4 py-3 text-center text-slate-600 dark:text-slate-400">{skill.phase_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-slate-500 dark:text-slate-400">No skills generated yet</p>
          )}
        </div>
      )}

      {/* Weights Tab */}
      {activeTab === 'weights' && (
        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-white mb-4">Weight Update History</h3>
          {weights.length > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={weights.map((w, i) => ({
                time: new Date(w.timestamp).toLocaleTimeString(),
                weight: w.weight_after,
              }))}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="time" stroke="#6b7280" />
                <YAxis stroke="#6b7280" />
                <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: 'none', borderRadius: '8px' }} />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="weight"
                  stroke={COLORS.primary}
                  strokeWidth={2}
                  dot={false}
                  name="Weight Value"
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-slate-500 dark:text-slate-400">No weight updates yet</p>
          )}
        </div>
      )}

      {/* Feedback Tab */}
      {activeTab === 'feedback' && (
        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-white mb-4">Feedback Impact Timeline</h3>
          {feedback.length > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={feedback.map((f, i) => ({
                time: new Date(f.timestamp).toLocaleTimeString(),
                impact: f.impact_on_loss,
                signal: f.signal,
              }))}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="time" stroke="#6b7280" />
                <YAxis stroke="#6b7280" />
                <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: 'none', borderRadius: '8px' }} />
                <Legend />
                <Bar
                  dataKey="impact"
                  fill={COLORS.primary}
                  name="Loss Impact"
                />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-slate-500 dark:text-slate-400">No feedback signals yet</p>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="mt-8 text-center text-sm text-slate-500 dark:text-slate-400">
        <p>Phase 4 • Audit Trail + Compliance Ready • Real-time Updates Every 5s</p>
      </div>
    </div>
  );
};

export default LearningDashboard;
