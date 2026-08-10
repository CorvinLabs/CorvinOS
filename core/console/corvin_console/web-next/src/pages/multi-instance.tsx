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
  auto_load_repo_enabled?: boolean;
}

export function MultiInstanceDashboard() {
  const [status, setStatus] = useState<MultiInstanceStatus | null>(null);
  const [patterns, setPatterns] = useState<LearningPattern[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [customRepo, setCustomRepo] = useState('');
  const [showRepoEditor, setShowRepoEditor] = useState(false);
  const [isDarkMode, setIsDarkMode] = useState(false);
  const [isInitialized, setIsInitialized] = useState(false);

  // Initialize repo from status once loaded (one-time only, gated by feature flag)
  useEffect(() => {
    if (status?.github_repo && !isInitialized && !customRepo && status.auto_load_repo_enabled) {
      setCustomRepo(status.github_repo);
      setIsInitialized(true);
    }
  }, [status?.github_repo, isInitialized, customRepo, status?.auto_load_repo_enabled]);

  // Detect dark mode from multiple sources
  useEffect(() => {
    const checkDarkMode = () => {
      // Check 1: HTML element dark class
      const hasDarkClass = document.documentElement.classList.contains('dark');

      // Check 2: Computed background color (dark mode = dark bg)
      const bodyBg = window.getComputedStyle(document.body).backgroundColor;
      // Dark mode typically has dark backgrounds like rgb(15, 23, 42) or similar
      const isDarkBg = bodyBg.includes('rgb') &&
        (() => {
          const match = bodyBg.match(/\d+/g);
          if (!match || match.length < 3) return false;
          const [r, g, b] = match.map(Number);
          return (r + g + b) / 3 < 128; // Average of RGB < 128 = dark
        })();

      setIsDarkMode(hasDarkClass || isDarkBg);
    };

    checkDarkMode();

    // Watch for class changes and listen to system preference changes
    const observer = new MutationObserver(checkDarkMode);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

    // Also watch body for background color changes
    const styleObserver = new MutationObserver(checkDarkMode);
    styleObserver.observe(document.body, { attributes: true, attributeFilter: ['style'] });

    return () => {
      observer.disconnect();
      styleObserver.disconnect();
    };
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

  // Theme colors
  const colors = {
    bg: isDarkMode ? '#020617' : '#ffffff',
    text: isDarkMode ? '#f1f5f9' : '#111827',
    cardBg: isDarkMode ? '#0f172a' : '#f9fafb',
    cardBorder: isDarkMode ? '#334155' : '#e5e7eb',
    label: isDarkMode ? '#94a3b8' : '#4b5563',
    accent: isDarkMode ? '#4ade80' : '#059669',
    error: isDarkMode ? '#fca5a5' : '#dc2626',
    errorBg: isDarkMode ? '#7f1d1d' : '#fee2e2',
    hover: isDarkMode ? '#1e293b' : '#f3f4f6'
  };

  if (loading) {
    return (
      <div style={{ backgroundColor: colors.bg, color: colors.text, padding: '24px', textAlign: 'center' }}>
        Loading multi-instance status...
      </div>
    );
  }

  if (error || !status) {
    return (
      <div
        style={{
          padding: '24px',
          borderRadius: '8px',
          border: `1px solid ${colors.cardBorder}`,
          backgroundColor: colors.errorBg,
          color: colors.error
        }}
      >
        <AlertCircle style={{ marginBottom: '8px' }} size={20} />
        <p>{error || 'Failed to load data'}</p>
      </div>
    );
  }

  return (
    <div style={{ backgroundColor: colors.bg, color: colors.text, padding: '24px' }}>
      <style>{`
        body { background-color: ${colors.bg}; color: ${colors.text}; }
      `}</style>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 'bold', color: colors.accent, margin: 0 }}>
          🧠 Cross-Device Learning
        </h1>
        <button
          onClick={() => setShowRepoEditor(!showRepoEditor)}
          style={{
            padding: '8px',
            backgroundColor: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: colors.accent,
            borderRadius: '6px'
          }}
          onMouseEnter={(e) => e.currentTarget.style.backgroundColor = colors.hover}
          onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
        >
          <Settings size={20} />
        </button>
      </div>

      {/* GitHub Repo Editor */}
      {showRepoEditor && (
        <div
          style={{
            padding: '16px',
            borderRadius: '8px',
            border: `1px solid ${colors.cardBorder}`,
            backgroundColor: colors.cardBg,
            marginBottom: '24px'
          }}
        >
          <label style={{ display: 'block', fontSize: '14px', fontWeight: '500', marginBottom: '8px', color: colors.label }}>
            GitHub Repository
          </label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              type="text"
              value={customRepo}
              onChange={(e) => setCustomRepo(e.target.value)}
              placeholder="https://github.com/user/repo"
              style={{
                flex: 1,
                padding: '8px 12px',
                borderRadius: '6px',
                border: `1px solid ${colors.cardBorder}`,
                backgroundColor: colors.cardBg,
                color: colors.text
              }}
            />
            <a
              href={getGithubRepo()}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                padding: '8px 16px',
                borderRadius: '6px',
                backgroundColor: colors.cardBorder,
                color: colors.text,
                textDecoration: 'none',
                fontWeight: '500',
                cursor: 'pointer'
              }}
            >
              Open
            </a>
          </div>
        </div>
      )}

      {/* Instance Status Section */}
      <div
        style={{
          border: `1px solid ${colors.cardBorder}`,
          borderRadius: '8px',
          padding: '16px',
          backgroundColor: colors.cardBg,
          marginBottom: '24px'
        }}
      >
        <h2 style={{ fontSize: '18px', fontWeight: '600', marginBottom: '16px', color: colors.accent, display: 'flex', alignItems: 'center', gap: '8px', margin: '0 0 16px 0' }}>
          <Check size={20} />
          Instance Status
        </h2>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {status.instances.map((instance) => (
            <div
              key={instance.instance_id}
              style={{
                border: `1px solid ${colors.cardBorder}`,
                borderRadius: '6px',
                padding: '12px',
                backgroundColor: colors.bg,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center'
              }}
            >
              <div>
                <p style={{ fontWeight: '500', margin: '0 0 4px 0' }}>{instance.name}</p>
                <p style={{ fontSize: '14px', color: colors.label, margin: '0' }}>{instance.instance_id}</p>
              </div>
              <div style={{ textAlign: 'right' }}>
                <p
                  style={{
                    fontSize: '14px',
                    fontWeight: '500',
                    color: instance.status === 'active' ? colors.accent : '#fbbf24',
                    margin: '0 0 4px 0'
                  }}
                >
                  {instance.status.toUpperCase()}
                </p>
                <p style={{ fontSize: '12px', color: colors.label, margin: '0' }}>
                  Last: {new Date(instance.last_seen).toLocaleTimeString()}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Patterns Section */}
      <div
        style={{
          border: `1px solid ${colors.cardBorder}`,
          borderRadius: '8px',
          padding: '16px',
          backgroundColor: colors.cardBg,
          marginBottom: '24px'
        }}
      >
        <h2 style={{ fontSize: '18px', fontWeight: '600', marginBottom: '16px', color: colors.accent, display: 'flex', alignItems: 'center', gap: '8px', margin: '0 0 16px 0' }}>
          <Clock size={20} />
          Learned Patterns ({patterns.length})
        </h2>

        {patterns.length === 0 ? (
          <p style={{ color: colors.label, margin: '0' }}>No patterns learned yet</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {patterns.map((pattern) => (
              <div
                key={pattern.pattern_id}
                style={{
                  border: `1px solid ${colors.cardBorder}`,
                  borderRadius: '6px',
                  padding: '12px',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'flex-start'
                }}
              >
                <div style={{ flex: 1 }}>
                  <p style={{ fontFamily: 'monospace', fontSize: '12px', color: colors.label, margin: '0 0 8px 0' }}>
                    {pattern.pattern_id}
                  </p>
                  <p style={{ fontSize: '14px', margin: '0 0 4px 0' }}>
                    <span style={{ fontWeight: '500' }}>Recommendation:</span> {pattern.recommended}
                  </p>
                  <p style={{ fontSize: '14px', margin: '0' }}>
                    <span style={{ fontWeight: '500' }}>Confidence:</span>{' '}
                    <span style={{ color: colors.accent }}>{(pattern.confidence * 100).toFixed(0)}%</span>
                  </p>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <p style={{ fontSize: '12px', color: colors.label, margin: '0 0 4px 0' }}>Sources:</p>
                  <ul style={{ margin: '0', padding: '0', listStyle: 'none' }}>
                    {pattern.sources.map((src) => (
                      <li key={src} style={{ fontSize: '12px', fontFamily: 'monospace', color: colors.label }}>
                        {src}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Sync Health Section */}
      <div
        style={{
          border: `1px solid ${colors.cardBorder}`,
          borderRadius: '8px',
          padding: '16px',
          backgroundColor: colors.cardBg
        }}
      >
        <h2 style={{ fontSize: '18px', fontWeight: '600', marginBottom: '16px', color: colors.accent, display: 'flex', alignItems: 'center', gap: '8px', margin: '0 0 16px 0' }}>
          <Github size={20} />
          Sync Health
        </h2>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: colors.label }}>Merged State:</span>
            <span style={{ fontFamily: 'monospace', fontSize: '14px' }}>{status.freshness}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: colors.label }}>Sync Frequency:</span>
            <span style={{ fontFamily: 'monospace', fontSize: '14px' }}>{status.sync_frequency}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: colors.label }}>Repository:</span>
            <a
              href={getGithubRepo()}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: colors.accent, textDecoration: 'none', cursor: 'pointer' }}
            >
              GitHub ↗
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

export default MultiInstanceDashboard;
