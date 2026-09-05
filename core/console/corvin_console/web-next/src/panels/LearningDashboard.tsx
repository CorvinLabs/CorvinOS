/**
 * Learning Dashboard: Method Discovery & Autonomous Learning Panel
 *
 * Displays:
 * - Discovered patterns (confidence, success rate)
 * - Skill config version history (rollback options)
 * - User feedback input (rate tasks)
 * - Learned preferences per task type
 * - Recommendations based on workstyle
 */

import React, { useState, useEffect } from 'react';

interface Pattern {
  pattern_id: string;
  task_type: string;
  skill_sequence: string[];
  confidence_score: number;
  success_rate: number;
  observation_count: number;
}

interface ConfigVersion {
  version_id: string;
  timestamp: string;
  change_reason: string;
  improvement_pct: number;
  user_can_undo: boolean;
}

interface Preference {
  task_type: string;
  confidence_score: number;
  preferred_skills: Record<string, number>;
  observation_count: number;
}

export const LearningDashboard: React.FC = () => {
  const [patterns, setPatterns] = useState<Pattern[]>([]);
  const [configVersions, setConfigVersions] = useState<ConfigVersion[]>([]);
  const [preferences, setPreferences] = useState<Record<string, Preference>>({});
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'patterns' | 'config' | 'preferences' | 'summary'>('summary');
  const [feedbackTask, setFeedbackTask] = useState('');
  const [feedbackQuality, setFeedbackQuality] = useState<'excellent' | 'good' | 'okay' | 'poor' | 'bad'>('good');

  // Fetch data on mount
  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  const fetchData = async () => {
    try {
      const [patternsRes, versionsRes, prefsRes] = await Promise.all([
        fetch('/v1/console/learning/patterns'),
        fetch('/v1/console/learning/config-versions'),
        fetch('/v1/console/learning/preferences'),
      ]);

      if (patternsRes.ok) {
        const data = await patternsRes.json();
        setPatterns(data.data || data || []);
      }
      if (versionsRes.ok) {
        const data = await versionsRes.json();
        setConfigVersions(data.data || data || []);
      }
      if (prefsRes.ok) {
        const data = await prefsRes.json();
        setPreferences(data.data || data || {});
      }
    } catch (error) {
      console.error('Failed to fetch learning data:', error);
    } finally {
      setLoading(false);
    }
  };

  const submitFeedback = async () => {
    if (!feedbackTask) return;

    try {
      const res = await fetch('/v1/console/learning/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: feedbackTask,
          outcome_quality: feedbackQuality,
          would_repeat: feedbackQuality === 'excellent' || feedbackQuality === 'good',
        }),
      });

      if (res.ok) {
        setFeedbackTask('');
        setTimeout(fetchData, 1000); // Refresh after feedback
      }
    } catch (error) {
      console.error('Failed to submit feedback:', error);
    }
  };

  const rollbackConfig = async (version: string) => {
    if (!window.confirm(`Rollback to ${version}?`)) return;

    try {
      const res = await fetch('/v1/console/learning/config/rollback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to_version: version }),
      });

      if (res.ok) {
        setTimeout(fetchData, 1000);
      }
    } catch (error) {
      console.error('Rollback failed:', error);
    }
  };

  if (loading) {
    return <div className="p-4">Loading learning dashboard...</div>;
  }

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-3xl font-bold mb-6">🧠 Learning Dashboard</h1>

      {/* Tab Navigation */}
      <div className="flex gap-4 mb-6 border-b overflow-x-auto">
        {(['summary', 'patterns', 'config', 'preferences'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`pb-2 px-4 font-semibold whitespace-nowrap ${
              activeTab === tab
                ? 'border-b-2 border-blue-500 text-blue-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            {tab === 'summary' && `📝 Summary`}
            {tab === 'patterns' && `📊 Patterns (${patterns.length})`}
            {tab === 'config' && `⚙️ Config History (${configVersions.length})`}
            {tab === 'preferences' && `👤 Preferences (${Object.keys(preferences).length})`}
          </button>
        ))}
      </div>

      {/* TAB 1: Learning Summary */}
      {activeTab === 'summary' && (
        <div>
          <h2 className="text-2xl font-bold mb-6">📝 Learning Summary</h2>

          {patterns.length === 0 && configVersions.length === 0 && Object.keys(preferences).length === 0 ? (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-6 text-center">
              <p className="text-lg text-gray-700 mb-2">No learning data yet</p>
              <p className="text-sm text-gray-600">Complete tasks and provide feedback to see what the system learns about your working style.</p>
            </div>
          ) : (
            <div className="space-y-6">
              {/* Overall Stats */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="bg-gradient-to-br from-blue-50 to-blue-100 rounded-lg p-4 border border-blue-200">
                  <div className="text-3xl font-bold text-blue-600">{patterns.length}</div>
                  <div className="text-sm text-gray-700">Patterns Discovered</div>
                  <p className="text-xs text-gray-600 mt-2">
                    {patterns.length === 0
                      ? 'Need 5+ tasks to discover patterns'
                      : `Based on ${patterns.reduce((sum, p) => sum + p.observation_count, 0)} observations`}
                  </p>
                </div>

                <div className="bg-gradient-to-br from-green-50 to-green-100 rounded-lg p-4 border border-green-200">
                  <div className="text-3xl font-bold text-green-600">
                    {configVersions.length > 1 ? `+${((configVersions[configVersions.length - 1]?.improvement_pct || 0) * 100).toFixed(1)}%` : 'N/A'}
                  </div>
                  <div className="text-sm text-gray-700">Config Improvement</div>
                  <p className="text-xs text-gray-600 mt-2">
                    {configVersions.length === 0
                      ? 'Will improve after feedback'
                      : `${configVersions.length} config versions tested`}
                  </p>
                </div>

                <div className="bg-gradient-to-br from-purple-50 to-purple-100 rounded-lg p-4 border border-purple-200">
                  <div className="text-3xl font-bold text-purple-600">{Object.keys(preferences).length}</div>
                  <div className="text-sm text-gray-700">Task Types Learned</div>
                  <p className="text-xs text-gray-600 mt-2">
                    {Object.keys(preferences).length === 0
                      ? 'Preferences emerge after 10+ tasks'
                      : Object.keys(preferences).map(t => t.charAt(0).toUpperCase() + t.slice(1)).join(', ')}
                  </p>
                </div>
              </div>

              {/* Main Learnings */}
              <div className="bg-white rounded-lg p-6 border border-gray-200">
                <h3 className="text-lg font-semibold mb-4">🎯 What We've Learned About You</h3>

                {patterns.length > 0 && (
                  <div className="mb-6">
                    <h4 className="font-semibold text-gray-800 mb-3">Your Most Effective Workflows</h4>
                    <div className="space-y-3">
                      {patterns.slice(0, 3).map(p => (
                        <div key={p.pattern_id} className="bg-gray-50 rounded p-4 border-l-4 border-blue-500">
                          <p className="font-mono text-sm mb-2 text-gray-700">
                            {p.skill_sequence.join(' → ')}
                          </p>
                          <div className="flex justify-between text-xs text-gray-600">
                            <span>✓ {(p.success_rate * 100).toFixed(0)}% success rate</span>
                            <span>→ {(p.confidence_score * 100).toFixed(0)}% confidence</span>
                          </div>
                          <p className="text-xs text-gray-500 mt-2">
                            Based on {p.observation_count} {p.observation_count === 1 ? 'task' : 'tasks'}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {configVersions.length > 1 && (
                  <div className="mb-6">
                    <h4 className="font-semibold text-gray-800 mb-3">Configuration Evolution</h4>
                    <div className="bg-gray-50 rounded p-4">
                      <p className="text-sm text-gray-700 mb-3">
                        The system has optimized your configuration through {configVersions.length} iterations:
                      </p>
                      <div className="space-y-2">
                        {configVersions.slice(-3).map((v, idx) => (
                          <div key={v.version_id} className="text-sm text-gray-600 flex justify-between">
                            <span>{v.version_id}: {v.change_reason}</span>
                            <span className="text-green-600 font-semibold">+{v.improvement_pct.toFixed(1)}%</span>
                          </div>
                        ))}
                      </div>
                      <p className="text-xs text-gray-500 mt-3">
                        💡 Tip: You can rollback to any previous version if you prefer it.
                      </p>
                    </div>
                  </div>
                )}

                {Object.keys(preferences).length > 0 && (
                  <div className="mb-4">
                    <h4 className="font-semibold text-gray-800 mb-3">Your Work Style</h4>
                    <div className="space-y-4">
                      {Object.entries(preferences).map(([taskType, prefs]) => (
                        <div key={taskType} className="bg-gray-50 rounded p-4">
                          <p className="font-semibold text-gray-800 mb-2">
                            For {taskType.charAt(0).toUpperCase() + taskType.slice(1)} Tasks
                          </p>
                          <p className="text-sm text-gray-700 mb-3">
                            You prefer this sequence:
                          </p>
                          <p className="font-mono text-sm bg-white px-3 py-2 rounded border border-gray-200 mb-2">
                            {Object.entries(prefs.preferred_skills)
                              .sort(([, a], [, b]) => b - a)
                              .map(([skill]) => skill)
                              .join(' → ')}
                          </p>
                          <p className="text-xs text-gray-600">
                            Confidence: {(prefs.confidence_score * 100).toFixed(0)}% (based on {prefs.observation_count} tasks)
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Next Steps */}
              <div className="bg-gradient-to-r from-amber-50 to-orange-50 rounded-lg p-6 border border-amber-200">
                <h3 className="text-lg font-semibold mb-3">💡 Next Steps</h3>
                <ul className="space-y-2 text-sm text-gray-700">
                  {patterns.length === 0 && (
                    <li className="flex items-start gap-2">
                      <span className="text-amber-600 font-bold">1.</span>
                      <span>Complete 5–10 similar tasks and provide feedback to discover your patterns</span>
                    </li>
                  )}
                  {patterns.length > 0 && configVersions.length === 0 && (
                    <li className="flex items-start gap-2">
                      <span className="text-amber-600 font-bold">2.</span>
                      <span>Rate your completed tasks to help the system optimize your configuration</span>
                    </li>
                  )}
                  {configVersions.length > 0 && Object.keys(preferences).length === 0 && (
                    <li className="flex items-start gap-2">
                      <span className="text-amber-600 font-bold">3.</span>
                      <span>Continue providing feedback so the system learns your task-type preferences</span>
                    </li>
                  )}
                  {patterns.length > 0 && (
                    <li className="flex items-start gap-2">
                      <span className="text-blue-600 font-bold">→</span>
                      <span>The system will automatically recommend your preferred workflows for new tasks</span>
                    </li>
                  )}
                </ul>
              </div>
            </div>
          )}
        </div>
      )}

      {/* TAB 2: Patterns */}
      {activeTab === 'patterns' && (
        <div>
          <h2 className="text-2xl font-bold mb-4">Discovered Patterns</h2>
          {patterns.length === 0 ? (
            <p className="text-gray-600">No patterns discovered yet. Complete more tasks to learn patterns.</p>
          ) : (
            <div className="grid gap-4">
              {patterns.map(p => (
                <div key={p.pattern_id} className="border rounded-lg p-4 bg-gray-50">
                  <div className="flex justify-between items-start mb-3">
                    <div>
                      <h3 className="font-semibold text-lg">{p.task_type.toUpperCase()}</h3>
                      <p className="text-sm text-gray-600">{p.pattern_id}</p>
                    </div>
                    <div className="text-right">
                      <div className="text-2xl font-bold text-blue-600">{(p.confidence_score * 100).toFixed(0)}%</div>
                      <div className="text-xs text-gray-600">confidence</div>
                    </div>
                  </div>

                  <div className="mb-3">
                    <p className="text-sm font-mono bg-white px-3 py-2 rounded border">
                      {p.skill_sequence.join(' → ')}
                    </p>
                  </div>

                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <span className="text-gray-600">Success Rate:</span>
                      <div className="font-semibold">{(p.success_rate * 100).toFixed(0)}%</div>
                    </div>
                    <div>
                      <span className="text-gray-600">Observations:</span>
                      <div className="font-semibold">{p.observation_count}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Feedback Input */}
          <div className="mt-8 border-t pt-6">
            <h3 className="text-xl font-bold mb-4">💬 Rate Your Tasks</h3>
            <div className="flex gap-3">
              <input
                type="text"
                placeholder="Enter task ID (e.g., feature_auth_v1)"
                value={feedbackTask}
                onChange={e => setFeedbackTask(e.target.value)}
                className="flex-1 px-3 py-2 border rounded"
              />
              <select
                value={feedbackQuality}
                onChange={e => setFeedbackQuality(e.target.value as any)}
                className="px-3 py-2 border rounded"
              >
                <option value="excellent">Excellent</option>
                <option value="good">Good</option>
                <option value="okay">Okay</option>
                <option value="poor">Poor</option>
                <option value="bad">Bad</option>
              </select>
              <button
                onClick={submitFeedback}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
              >
                Submit
              </button>
            </div>
          </div>
        </div>
      )}

      {/* TAB 3: Config History */}
      {activeTab === 'config' && (
        <div>
          <h2 className="text-2xl font-bold mb-4">Skill Config History (os.delegation_router)</h2>
          {configVersions.length === 0 ? (
            <p className="text-gray-600">No config changes yet. Submit feedback to trigger learning.</p>
          ) : (
            <div className="space-y-3">
              {configVersions.map((v, idx) => (
                <div key={v.version_id} className="border rounded-lg p-4 bg-gray-50">
                  <div className="flex justify-between items-center">
                    <div>
                      <h3 className="font-semibold">{v.version_id}</h3>
                      <p className="text-sm text-gray-600">{v.change_reason}</p>
                      <p className="text-xs text-gray-500">{new Date(v.timestamp).toLocaleString()}</p>
                    </div>
                    <div className="text-right flex items-center gap-4">
                      <div>
                        <div className="text-lg font-bold text-green-600">+{v.improvement_pct.toFixed(1)}%</div>
                        <div className="text-xs text-gray-600">improvement</div>
                      </div>
                      {v.user_can_undo && idx > 0 && (
                        <button
                          onClick={() => rollbackConfig(v.version_id)}
                          className="px-3 py-1 bg-red-100 text-red-700 rounded text-sm hover:bg-red-200"
                        >
                          Rollback
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* TAB 4: Preferences */}
      {activeTab === 'preferences' && (
        <div>
          <h2 className="text-2xl font-bold mb-4">Learned Preferences</h2>
          {Object.keys(preferences).length === 0 ? (
            <p className="text-gray-600">No preferences learned yet. Complete tasks and provide feedback.</p>
          ) : (
            <div className="grid gap-4">
              {Object.entries(preferences).map(([taskType, prefs]) => (
                <div key={taskType} className="border rounded-lg p-4 bg-gradient-to-r from-blue-50 to-purple-50">
                  <div className="flex justify-between items-start mb-3">
                    <h3 className="text-lg font-semibold">{taskType.toUpperCase()} Tasks</h3>
                    <div className="text-right">
                      <div className="text-xl font-bold text-purple-600">{(prefs.confidence_score * 100).toFixed(0)}%</div>
                      <div className="text-xs text-gray-600">confidence (N={prefs.observation_count})</div>
                    </div>
                  </div>

                  <div>
                    <p className="text-sm font-semibold mb-2">Preferred Skills:</p>
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(prefs.preferred_skills)
                        .sort(([, a], [, b]) => b - a)
                        .map(([skill, score]) => (
                          <div key={skill} className="bg-white px-3 py-1 rounded text-sm border">
                            {skill} <span className="text-gray-500">({(score * 100).toFixed(0)}%)</span>
                          </div>
                        ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="mt-8 pt-6 border-t text-xs text-gray-500">
        <p>Last updated: {new Date().toLocaleTimeString()}</p>
        <p>🔄 Learning Dashboard auto-refreshes every 30 seconds</p>
      </div>
    </div>
  );
};

export default LearningDashboard;
