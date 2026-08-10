/**
 * Multi-Instance Learning Dashboard (ADR-0275/0277)
 *
 * Cross-device learning patterns, sync status, overrides.
 * Features: Dark Mode support, GitHub repo selector, live polling.
 */

import { useEffect, useState } from 'react';
import { AlertCircle, Check, Clock, Github, Settings } from 'lucide-react';

interface InstanceStatus {
  instance_id: string;
  name: string;
  status: 'active' | 'inactive' | 'retired' | 'archived';
  last_seen: string;
  is_primary: boolean;
}

interface LearningPattern {
  pattern_id: string;
  recommended: string;
  confidence: number;
  sources: string[];
  candidates: Record<string, any>;
}

interface MultiInstanceStatus {
  enabled: boolean;
  instances: InstanceStatus[];
  merged_patterns: number;
  merged_at: string;
  freshness: string;
  github_repo: string;
  sync_frequency: string;
}

export function MultiInstanceDashboard() {
  const [status, setStatus] = useState<MultiInstanceStatus | null>(null);
  const [patterns, setPatterns] = useState<LearningPattern[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [customRepo, setCustomRepo] = useState('');
  const [showRepoEditor, setShowRepoEditor] = useState(false);
  const [isDarkMode, setIsDarkMode] = useState(false);

  // Detect dark mode from document class
  useEffect(() => {
    const checkDarkMode = () => {
      setIsDarkMode(document.documentElement.classList.contains('dark'));
    };
    checkDarkMode();
    const observer = new MutationObserver(checkDarkMode);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  const getGithubRepo = () => customRepo || (status?.github_repo || 'https://github.com/veegee82/tenent-shumway');

  useEffect(() => {
    const fetchData = async () => {
      try {
        let apiBase = window.location.origin;

        if (window.location.hostname === 'localhost' &&
            (window.location.port === '3000' || window.location.port === '5173')) {
          const ports = ['8765', '8000', '8080'];
          let foundPort = null;

          for (const port of ports) {
            try {
              const testRes = await fetch(`http://localhost:${port}/v1/console/api/multi-instance/status`, {
                method: 'HEAD'
              });
              if (testRes.ok || testRes.status === 404) {
                foundPort = port;
                break;
              }
            } catch (e) {
              continue;
            }
          }

          if (foundPort) {
            apiBase = `http://localhost:${foundPort}`;
          }
        }

        const [statusRes, patternsRes] = await Promise.all([
          fetch(`${apiBase}/v1/console/api/multi-instance/status`),
          fetch(`${apiBase}/v1/console/api/multi-instance/patterns`),
        ]);

        if (!statusRes.ok || !patternsRes.ok) {
          throw new Error(`Failed to fetch from ${apiBase}`);
        }

        const statusData = await statusRes.json();
        const patternsData = await patternsRes.json();

        setStatus(statusData);
        setPatterns(patternsData.patterns || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  const bgClass = isDarkMode ? 'bg-slate-950 text-slate-100' : 'bg-white text-gray-900';
  const cardClass = isDarkMode
    ? 'bg-slate-900 border-slate-700'
    : 'bg-gray-50 border-gray-200';
  const labelClass = isDarkMode ? 'text-slate-400' : 'text-gray-600';
  const accentClass = isDarkMode ? 'text-emerald-400' : 'text-emerald-600';

  if (loading) {
    return <div className={`p-6 text-center ${bgClass}`}>Loading multi-instance status...</div>;
  }

  if (error || !status) {
    return (
      <div className={`p-6 rounded ${bgClass} ${isDarkMode ? 'bg-red-950' : 'bg-red-50'} border ${isDarkMode ? 'border-red-800' : 'border-red-200'}`}>
        <AlertCircle className={`${isDarkMode ? 'text-red-400' : 'text-red-600'} mb-2`} />
        <p className={isDarkMode ? 'text-red-400' : 'text-red-600'}>{error || 'Failed to load data'}</p>
      </div>
    );
  }

  return (
    <div className={`p-6 space-y-6 ${bgClass}`}>
      <div className="flex justify-between items-center">
        <h1 className={`text-2xl font-bold ${accentClass}`}>🧠 Cross-Device Learning</h1>
        <button
          onClick={() => setShowRepoEditor(!showRepoEditor)}
          className={`p-2 rounded transition ${isDarkMode ? 'hover:bg-slate-800' : 'hover:bg-gray-100'}`}
        >
          <Settings size={20} />
        </button>
      </div>

      {/* GitHub Repo Editor */}
      {showRepoEditor && (
        <div className={`p-4 rounded border ${cardClass}`}>
          <label className={`block text-sm font-medium mb-2 ${labelClass}`}>GitHub Repository</label>
          <div className="flex gap-2">
            <input
              type="text"
              value={customRepo}
              onChange={(e) => setCustomRepo(e.target.value)}
              placeholder="https://github.com/user/repo"
              className={`flex-1 px-3 py-2 rounded border ${isDarkMode ? 'bg-slate-800 border-slate-600' : 'bg-white border-gray-300'}`}
            />
            <a
              href={getGithubRepo()}
              target="_blank"
              rel="noopener noreferrer"
              className={`px-4 py-2 rounded font-medium transition ${isDarkMode ? 'bg-slate-700 hover:bg-slate-600' : 'bg-gray-200 hover:bg-gray-300'}`}
            >
              Open
            </a>
          </div>
          <p className={`text-xs mt-2 ${labelClass}`}>
            Default: {status?.github_repo || 'https://github.com/veegee82/tenent-shumway'}
          </p>
        </div>
      )}

      {/* Instance Status Section */}
      <section className={`border rounded-lg p-4 ${cardClass}`}>
        <h2 className={`text-lg font-semibold mb-4 flex items-center gap-2 ${accentClass}`}>
          <Check size={20} />
          Instance Status
        </h2>

        <div className="space-y-2">
          {status.instances.map((instance) => (
            <div key={instance.instance_id} className={`border rounded p-3 ${isDarkMode ? 'bg-slate-800 border-slate-700' : 'bg-white border-gray-200'}`}>
              <div className="flex justify-between items-center">
                <div>
                  <p className="font-medium">{instance.name}</p>
                  <p className={`text-sm ${labelClass}`}>{instance.instance_id}</p>
                </div>
                <div className="text-right">
                  <p className={`text-sm font-medium ${
                    instance.status === 'active' ? accentClass : 'text-yellow-500'
                  }`}>
                    {instance.status.toUpperCase()}
                  </p>
                  <p className={`text-xs ${labelClass}`}>Last: {new Date(instance.last_seen).toLocaleTimeString()}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Patterns Section */}
      <section className={`border rounded-lg p-4 ${cardClass}`}>
        <h2 className={`text-lg font-semibold mb-4 flex items-center gap-2 ${accentClass}`}>
          <Clock size={20} />
          Learned Patterns ({patterns.length})
        </h2>

        {patterns.length === 0 ? (
          <p className={labelClass}>No patterns learned yet</p>
        ) : (
          <div className="space-y-3">
            {patterns.map((pattern) => (
              <div key={pattern.pattern_id} className={`border rounded p-3 ${isDarkMode ? 'border-slate-700' : 'border-gray-200'}`}>
                <div className="flex justify-between items-start">
                  <div className="flex-1">
                    <p className={`font-mono text-sm ${labelClass}`}>{pattern.pattern_id}</p>
                    <p className="text-sm mt-1">
                      <span className="font-medium">Recommendation:</span> {pattern.recommended}
                    </p>
                    <p className="text-sm">
                      <span className="font-medium">Confidence:</span>{' '}
                      <span className={accentClass}>{(pattern.confidence * 100).toFixed(0)}%</span>
                    </p>
                  </div>
                  <div className="text-right">
                    <p className={`text-xs ${labelClass}`}>Sources:</p>
                    <ul className={`text-xs font-mono ${labelClass}`}>
                      {pattern.sources.map((src) => (
                        <li key={src}>{src}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Sync Health Section */}
      <section className={`border rounded-lg p-4 ${cardClass}`}>
        <h2 className={`text-lg font-semibold mb-4 flex items-center gap-2 ${accentClass}`}>
          <Github size={20} />
          Sync Health
        </h2>

        <div className="space-y-2">
          <div className="flex justify-between">
            <span className={labelClass}>Merged State:</span>
            <span className="font-mono text-sm">{status.freshness}</span>
          </div>
          <div className="flex justify-between">
            <span className={labelClass}>Sync Frequency:</span>
            <span className="font-mono text-sm">{status.sync_frequency}</span>
          </div>
          <div className="flex justify-between">
            <span className={labelClass}>Repository:</span>
            <a href={getGithubRepo()} target="_blank" rel="noopener noreferrer" className={`${accentClass} hover:underline`}>
              GitHub ↗
            </a>
          </div>
        </div>
      </section>
    </div>
  );
}

export default MultiInstanceDashboard;
