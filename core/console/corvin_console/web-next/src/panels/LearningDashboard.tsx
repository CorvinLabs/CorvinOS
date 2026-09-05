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

// ============================================================================
// PALETTE (Same as Maturity Metrics)
// ============================================================================
const PALETTE = {
  systems: [
    { id: 'delegation_router', name: 'Delegation Router', hex: '#FF6B6B' },
    { id: 'context_adapter', name: 'Context Adapter', hex: '#4ECDC4' },
    { id: 'workflow_optimizer', name: 'Workflow Optimizer', hex: '#95E1D3' },
    { id: 'security_orchestrator', name: 'Security Orchestrator', hex: '#F7B801' },
    { id: 'flow_guard', name: 'Flow Guard', hex: '#6C63FF' },
  ],
  sequential: {
    light: '#F0F0F0',
    mid: '#888888',
    dark: '#1A1A1A',
  },
  status: {
    good: '#2ECC71',
    warning: '#F39C12',
    serious: '#E74C3C',
  },
  surface: {
    dark: '#0D1117',
    card: '#161B22',
    border: '#30363D',
    text: '#C9D1D9',
    muted: '#8B949E',
  },
};

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

interface LearningDashboardProps {
  activeTab?: 'patterns' | 'config' | 'preferences' | 'summary';
  onTabChange?: (tab: 'patterns' | 'config' | 'preferences' | 'summary') => void;
  hideTabNavigation?: boolean;
}

export const LearningDashboard: React.FC<LearningDashboardProps> = ({
  activeTab: propActiveTab,
  onTabChange,
  hideTabNavigation = false
}) => {
  const [patterns, setPatterns] = useState<Pattern[]>([]);
  const [configVersions, setConfigVersions] = useState<ConfigVersion[]>([]);
  const [preferences, setPreferences] = useState<Record<string, Preference>>({});
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'patterns' | 'config' | 'preferences' | 'summary'>(propActiveTab || 'summary');

  const handleTabChange = (tab: 'patterns' | 'config' | 'preferences' | 'summary') => {
    setActiveTab(tab);
    if (onTabChange) onTabChange(tab);
  };
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
        const patterns = data.data || data;
        if (Array.isArray(patterns)) {
          setPatterns(patterns);
        } else if (patterns && typeof patterns === 'object' && !patterns.detail) {
          setPatterns(Array.isArray(patterns) ? patterns : []);
        }
      }
      if (versionsRes.ok) {
        const data = await versionsRes.json();
        const versions = data.data || data;
        if (Array.isArray(versions)) {
          setConfigVersions(versions);
        }
      }
      if (prefsRes.ok) {
        const data = await prefsRes.json();
        const prefs = data.data || data;
        if (prefs && typeof prefs === 'object' && !prefs.detail) {
          setPreferences(prefs);
        }
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
    <div style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto', backgroundColor: PALETTE.surface.dark, color: PALETTE.surface.text }}>
      <h1 style={{ fontSize: '28px', fontWeight: 700, marginBottom: '24px', color: PALETTE.surface.text }}>🧠 Learning Dashboard</h1>

      {/* Tab Navigation */}
      {!hideTabNavigation && (
        <div style={{ display: 'flex', gap: '16px', marginBottom: '24px', borderBottom: `1px solid ${PALETTE.surface.border}`, overflowX: 'auto' }}>
          {(['summary', 'patterns', 'config', 'preferences'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => handleTabChange(tab)}
              style={{
                paddingBottom: '8px',
                paddingLeft: '16px',
                paddingRight: '16px',
                fontWeight: 600,
                whiteSpace: 'nowrap',
                borderBottom: activeTab === tab ? `2px solid #4ECDC4` : 'none',
                color: activeTab === tab ? '#4ECDC4' : PALETTE.surface.muted,
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                transition: 'color 0.2s',
              }}
            >
              {tab === 'summary' && `📝 Summary`}
              {tab === 'patterns' && `📊 Patterns (${patterns.length})`}
              {tab === 'config' && `⚙️ Config History (${configVersions.length})`}
              {tab === 'preferences' && `👤 Preferences (${Object.keys(preferences).length})`}
            </button>
          ))}
        </div>
      )}

      {/* TAB 1: Learning Summary */}
      {activeTab === 'summary' && (
        <div>
          <h2 style={{ fontSize: '24px', fontWeight: 700, marginBottom: '24px', color: PALETTE.surface.text }}>📝 Learning Summary</h2>

          {patterns.length === 0 && configVersions.length === 0 && Object.keys(preferences).length === 0 ? (
            <div style={{ backgroundColor: PALETTE.surface.card, border: `1px solid ${PALETTE.surface.border}`, borderRadius: '8px', padding: '24px', textAlign: 'center' }}>
              <p style={{ fontSize: '18px', color: PALETTE.surface.text, marginBottom: '8px' }}>No learning data yet</p>
              <p style={{ fontSize: '14px', color: PALETTE.surface.muted }}>Complete tasks and provide feedback to see what the system learns about your working style.</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
              {/* Overall Stats */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '16px', marginBottom: '24px' }}>
                <div style={{ backgroundColor: PALETTE.surface.card, borderRadius: '8px', padding: '16px', border: `1px solid ${PALETTE.surface.border}` }}>
                  <div style={{ fontSize: '32px', fontWeight: 700, color: PALETTE.systems[1].hex, marginBottom: '8px' }}>{patterns.length}</div>
                  <div style={{ fontSize: '14px', color: PALETTE.surface.text, marginBottom: '8px' }}>Patterns Discovered</div>
                  <p style={{ fontSize: '12px', color: PALETTE.surface.muted, margin: 0 }}>
                    {patterns.length === 0
                      ? 'Need 5+ tasks to discover patterns'
                      : `Based on ${patterns.reduce((sum, p) => sum + p.observation_count, 0)} observations`}
                  </p>
                </div>

                <div style={{ backgroundColor: PALETTE.surface.card, borderRadius: '8px', padding: '16px', border: `1px solid ${PALETTE.surface.border}` }}>
                  <div style={{ fontSize: '32px', fontWeight: 700, color: PALETTE.status.good, marginBottom: '8px' }}>
                    {configVersions.length > 1 ? `+${((configVersions[configVersions.length - 1]?.improvement_pct || 0) * 100).toFixed(1)}%` : 'N/A'}
                  </div>
                  <div style={{ fontSize: '14px', color: PALETTE.surface.text, marginBottom: '8px' }}>Config Improvement</div>
                  <p style={{ fontSize: '12px', color: PALETTE.surface.muted, margin: 0 }}>
                    {configVersions.length === 0
                      ? 'Will improve after feedback'
                      : `${configVersions.length} config versions tested`}
                  </p>
                </div>

                <div style={{ backgroundColor: PALETTE.surface.card, borderRadius: '8px', padding: '16px', border: `1px solid ${PALETTE.surface.border}` }}>
                  <div style={{ fontSize: '32px', fontWeight: 700, color: PALETTE.systems[4].hex, marginBottom: '8px' }}>{Object.keys(preferences).length}</div>
                  <div style={{ fontSize: '14px', color: PALETTE.surface.text, marginBottom: '8px' }}>Task Types Learned</div>
                  <p style={{ fontSize: '12px', color: PALETTE.surface.muted, margin: 0 }}>
                    {Object.keys(preferences).length === 0
                      ? 'Preferences emerge after 10+ tasks'
                      : Object.keys(preferences).map(t => t.charAt(0).toUpperCase() + t.slice(1)).join(', ')}
                  </p>
                </div>
              </div>

              {/* Main Learnings */}
              <div style={{ backgroundColor: PALETTE.surface.card, borderRadius: '8px', padding: '24px', border: `1px solid ${PALETTE.surface.border}` }}>
                <h3 style={{ fontSize: '18px', fontWeight: 600, marginBottom: '16px', color: PALETTE.surface.text }}>🎯 What We've Learned About You</h3>

                {patterns.length > 0 && (
                  <div style={{ marginBottom: '24px' }}>
                    <h4 style={{ fontWeight: 600, color: PALETTE.surface.text, marginBottom: '12px' }}>Your Most Effective Workflows</h4>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                      {patterns.slice(0, 3).map(p => (
                        <div key={p.pattern_id} style={{ backgroundColor: 'rgba(255, 107, 107, 0.1)', borderRadius: '6px', padding: '16px', borderLeft: `4px solid ${PALETTE.systems[0].hex}` }}>
                          <p style={{ fontFamily: 'monospace', fontSize: '14px', marginBottom: '8px', color: PALETTE.surface.text, margin: 0 }}>
                            {p.skill_sequence.join(' → ')}
                          </p>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', color: PALETTE.surface.muted }}>
                            <span>✓ {(p.success_rate * 100).toFixed(0)}% success rate</span>
                            <span>→ {(p.confidence_score * 100).toFixed(0)}% confidence</span>
                          </div>
                          <p style={{ fontSize: '12px', color: PALETTE.surface.muted, marginTop: '8px', margin: 0 }}>
                            Based on {p.observation_count} {p.observation_count === 1 ? 'task' : 'tasks'}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {configVersions.length > 1 && (
                  <div style={{ marginBottom: '24px' }}>
                    <h4 style={{ fontWeight: 600, color: PALETTE.surface.text, marginBottom: '12px' }}>Configuration Evolution</h4>
                    <div style={{ backgroundColor: 'rgba(78, 205, 196, 0.1)', borderRadius: '6px', padding: '16px' }}>
                      <p style={{ fontSize: '14px', color: PALETTE.surface.text, marginBottom: '12px', margin: 0 }}>
                        The system has optimized your configuration through {configVersions.length} iterations:
                      </p>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {configVersions.slice(-3).map((v, idx) => (
                          <div key={v.version_id} style={{ fontSize: '14px', color: PALETTE.surface.muted, display: 'flex', justifyContent: 'space-between' }}>
                            <span>{v.version_id}: {v.change_reason}</span>
                            <span style={{ color: PALETTE.status.good, fontWeight: 600 }}>+{v.improvement_pct.toFixed(1)}%</span>
                          </div>
                        ))}
                      </div>
                      <p style={{ fontSize: '12px', color: PALETTE.surface.muted, marginTop: '12px', margin: 0 }}>
                        💡 Tip: You can rollback to any previous version if you prefer it.
                      </p>
                    </div>
                  </div>
                )}

                {Object.keys(preferences).length > 0 && (
                  <div style={{ marginBottom: '16px' }}>
                    <h4 style={{ fontWeight: 600, color: PALETTE.surface.text, marginBottom: '12px' }}>Your Work Style</h4>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                      {Object.entries(preferences).map(([taskType, prefs]) => (
                        <div key={taskType} style={{ backgroundColor: 'rgba(102, 99, 255, 0.1)', borderRadius: '6px', padding: '16px' }}>
                          <p style={{ fontWeight: 600, color: PALETTE.surface.text, marginBottom: '8px', margin: 0 }}>
                            For {taskType.charAt(0).toUpperCase() + taskType.slice(1)} Tasks
                          </p>
                          <p style={{ fontSize: '14px', color: PALETTE.surface.muted, marginBottom: '12px', margin: 0 }}>
                            You prefer this sequence:
                          </p>
                          <p style={{ fontFamily: 'monospace', fontSize: '14px', backgroundColor: PALETTE.surface.border, padding: '8px 12px', borderRadius: '4px', marginBottom: '8px', color: PALETTE.surface.text, margin: 0 }}>
                            {Object.entries(prefs.preferred_skills)
                              .sort(([, a], [, b]) => b - a)
                              .map(([skill]) => skill)
                              .join(' → ')}
                          </p>
                          <p style={{ fontSize: '12px', color: PALETTE.surface.muted, margin: 0 }}>
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
