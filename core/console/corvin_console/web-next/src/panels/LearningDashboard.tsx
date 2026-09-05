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
  const [activeTab, setActiveTab] = useState<'patterns' | 'config' | 'preferences'>('patterns');
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

      if (patternsRes.ok) setPatterns(await patternsRes.json());
      if (versionsRes.ok) setConfigVersions(await versionsRes.json());
      if (prefsRes.ok) setPreferences(await prefsRes.json());
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
      <div className="flex gap-4 mb-6 border-b">
        {(['patterns', 'config', 'preferences'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`pb-2 px-4 font-semibold ${
              activeTab === tab
                ? 'border-b-2 border-blue-500 text-blue-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            {tab === 'patterns' && `📊 Patterns (${patterns.length})`}
            {tab === 'config' && `⚙️ Config History (${configVersions.length})`}
            {tab === 'preferences' && `👤 Preferences (${Object.keys(preferences).length})`}
          </button>
        ))}
      </div>

      {/* TAB 1: Patterns */}
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

      {/* TAB 2: Config History */}
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

      {/* TAB 3: Preferences */}
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
