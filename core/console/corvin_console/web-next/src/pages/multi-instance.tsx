/**
 * Multi-Instance Learning Dashboard (ADR-0275/0277)
 *
 * Cross-device learning patterns, sync status, overrides.
 * Component skeleton for Phase 1 MVP.
 */

import { useEffect, useState } from 'react';
import { AlertCircle, Check, Clock, Github } from 'lucide-react';

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

  useEffect(() => {
    const fetchData = async () => {
      try {
        // Determine API base URL with fallback
        let apiBase = window.location.origin;

        // If running on dev server (localhost:3000/5173), try Console ports
        if (window.location.hostname === 'localhost' &&
            (window.location.port === '3000' || window.location.port === '5173')) {
          // Try common Console ports
          const ports = ['8765', '8000', '8080'];
          let foundPort = null;

          for (const port of ports) {
            try {
              const testRes = await fetch(`http://localhost:${port}/api/multi-instance/status`, {
                method: 'HEAD'
              });
              if (testRes.ok || testRes.status === 404) {
                // Server responded (even if not found) - this is our port
                foundPort = port;
                break;
              }
            } catch (e) {
              // Port not responding, try next
              continue;
            }
          }

          if (foundPort) {
            apiBase = `http://localhost:${foundPort}`;
          }
        }

        const [statusRes, patternsRes] = await Promise.all([
          fetch(`${apiBase}/api/multi-instance/status`),
          fetch(`${apiBase}/api/multi-instance/patterns`),
        ]);

        if (!statusRes.ok || !patternsRes.ok) {
          throw new Error(`Failed to fetch from ${apiBase} (status: ${statusRes.status}, ${patternsRes.status})`);
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
    // Poll every 5 minutes
    const interval = setInterval(fetchData, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return <div className="p-6 text-center">Loading multi-instance status...</div>;
  }

  if (error || !status) {
    return (
      <div className="p-6 bg-red-50 border border-red-200 rounded">
        <AlertCircle className="text-red-600 mb-2" />
        <p className="text-red-600">{error || 'Failed to load data'}</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-bold">Cross-Device Learning</h1>

      {/* Instance Status Section */}
      <section className="bg-white border rounded-lg p-4">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Check className="w-5 h-5 text-green-600" />
          Instance Status
        </h2>

        <div className="space-y-2">
          {status.instances.map((instance) => (
            <div key={instance.instance_id} className="border rounded p-3 bg-gray-50">
              <div className="flex justify-between items-center">
                <div>
                  <p className="font-medium">{instance.name}</p>
                  <p className="text-sm text-gray-600">{instance.instance_id}</p>
                </div>
                <div className="text-right">
                  <p className={`text-sm font-medium ${
                    instance.status === 'active' ? 'text-green-600' : 'text-yellow-600'
                  }`}>
                    {instance.status.toUpperCase()}
                  </p>
                  <p className="text-xs text-gray-600">Last: {instance.last_seen}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Patterns Section */}
      <section className="bg-white border rounded-lg p-4">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Clock className="w-5 h-5 text-blue-600" />
          Learned Patterns ({patterns.length})
        </h2>

        {patterns.length === 0 ? (
          <p className="text-gray-600">No patterns learned yet</p>
        ) : (
          <div className="space-y-3">
            {patterns.map((pattern) => (
              <div key={pattern.pattern_id} className="border rounded p-3">
                <div className="flex justify-between items-start">
                  <div className="flex-1">
                    <p className="font-mono text-sm text-gray-700">{pattern.pattern_id}</p>
                    <p className="text-sm mt-1">
                      <span className="font-medium">Recommendation:</span> {pattern.recommended}
                    </p>
                    <p className="text-sm">
                      <span className="font-medium">Confidence:</span>{' '}
                      <span className="text-blue-600">{(pattern.confidence * 100).toFixed(0)}%</span>
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-gray-600">Sources:</p>
                    <ul className="text-xs font-mono">
                      {pattern.sources.map((src) => (
                        <li key={src} className="text-gray-600">{src}</li>
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
      <section className="bg-white border rounded-lg p-4">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Github className="w-5 h-5 text-purple-600" />
          Sync Health
        </h2>

        <div className="space-y-2">
          <div className="flex justify-between">
            <span className="text-gray-700">Merged State:</span>
            <span className="font-mono text-sm">{status.freshness}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-700">Sync Frequency:</span>
            <span className="font-mono text-sm">{status.sync_frequency}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-700">Repository:</span>
            <a href={status.github_repo} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
              GitHub
            </a>
          </div>
        </div>
      </section>
    </div>
  );
}

export default MultiInstanceDashboard;
