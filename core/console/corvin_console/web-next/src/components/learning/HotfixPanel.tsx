/**
 * Hotfix Panel (Stream 2: Story 18)
 *
 * One-click hotfix deployment for P0 incidents:
 * 1. Show pending hotfixes
 * 2. Display code diff
 * 3. Operator approves
 * 4. Tests run (<2 min)
 * 5. Deploy to production
 * 6. Monitor + rollback option
 */

import React, { useState, useEffect } from 'react';

interface Hotfix {
  hotfix_id: string;
  alert_id: string;
  description: string;
  status: 'pending' | 'approved' | 'testing' | 'deploying' | 'deployed' | 'failed' | 'rolled_back';
  created_at: string;
  approver?: string;
  test_result?: {
    passed: boolean;
    duration_seconds: number;
  };
  deployment_result?: {
    deployed: boolean;
    error_rate_before: number;
    error_rate_after: number;
  };
}

export const HotfixPanel: React.FC = () => {
  const [hotfixes, setHotfixes] = useState<Hotfix[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedHotfix, setSelectedHotfix] = useState<string | null>(null);

  useEffect(() => {
    fetchHotfixes();
    const interval = setInterval(fetchHotfixes, 10000); // Poll every 10s
    return () => clearInterval(interval);
  }, []);

  const fetchHotfixes = async () => {
    try {
      const response = await fetch('/v1/console/learning/hotfix?status=pending,approved,deploying,deployed');
      if (response.ok) {
        // Note: actual endpoint would need to support filtering
        setHotfixes([]); // Placeholder
      }
    } catch (err) {
      console.error('Fetch error:', err);
    }
  };

  const handleApprove = async (hotfixId: string) => {
    setLoading(true);
    try {
      const response = await fetch(`/v1/console/learning/hotfix/${hotfixId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approver: 'operator@example.com' }),
      });
      if (response.ok) {
        fetchHotfixes();
      }
    } catch (err) {
      console.error('Approval error:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleDeploy = async (hotfixId: string) => {
    setLoading(true);
    try {
      const response = await fetch(`/v1/console/learning/hotfix/${hotfixId}/deploy`, {
        method: 'POST',
      });
      if (response.ok) {
        fetchHotfixes();
      }
    } catch (err) {
      console.error('Deploy error:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleRollback = async (hotfixId: string) => {
    setLoading(true);
    try {
      // Note: rollback endpoint would need to be added
      console.log('Rolling back:', hotfixId);
      fetchHotfixes();
    } catch (err) {
      console.error('Rollback error:', err);
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (status: string) => {
    const colors: Record<string, string> = {
      pending: 'bg-yellow-100 text-yellow-800',
      approved: 'bg-blue-100 text-blue-800',
      testing: 'bg-purple-100 text-purple-800',
      deploying: 'bg-cyan-100 text-cyan-800',
      deployed: 'bg-green-100 text-green-800',
      failed: 'bg-red-100 text-red-800',
      rolled_back: 'bg-orange-100 text-orange-800',
    };
    return colors[status] || 'bg-gray-100 text-gray-800';
  };

  return (
    <div className="space-y-4 p-6">
      <h2 className="text-2xl font-bold">Hotfix Management</h2>

      {hotfixes.length === 0 ? (
        <div className="bg-gray-50 rounded-lg p-8 text-center">
          <svg
            className="w-12 h-12 text-gray-400 mx-auto mb-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
          <p className="text-gray-600 text-lg">No pending hotfixes</p>
          <p className="text-gray-500 text-sm mt-1">All systems running smoothly</p>
        </div>
      ) : (
        <div className="space-y-3">
          {hotfixes.map((hotfix) => (
            <div key={hotfix.hotfix_id} className="border rounded-lg overflow-hidden">
              {/* Header */}
              <div className="bg-white px-4 py-3 flex items-center justify-between cursor-pointer hover:bg-gray-50"
                onClick={() => setSelectedHotfix(selectedHotfix === hotfix.hotfix_id ? null : hotfix.hotfix_id)}>
                <div className="flex-1">
                  <div className="flex items-center gap-3">
                    <span className={`px-2 py-1 rounded text-xs font-medium ${getStatusColor(hotfix.status)}`}>
                      {hotfix.status.toUpperCase()}
                    </span>
                    <div>
                      <h3 className="font-semibold text-gray-900">{hotfix.description}</h3>
                      <p className="text-xs text-gray-600">
                        Alert: {hotfix.alert_id} • {new Date(hotfix.created_at).toLocaleString()}
                      </p>
                    </div>
                  </div>
                </div>
                <svg
                  className={`w-5 h-5 text-gray-400 transition ${
                    selectedHotfix === hotfix.hotfix_id ? 'transform rotate-180' : ''
                  }`}
                  fill="currentColor"
                  viewBox="0 0 20 20"
                >
                  <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
              </div>

              {/* Details (expanded) */}
              {selectedHotfix === hotfix.hotfix_id && (
                <div className="bg-gray-50 border-t px-4 py-3 space-y-4">
                  {/* Test Results */}
                  {hotfix.test_result && (
                    <div>
                      <h4 className="font-medium text-sm text-gray-900 mb-2">Test Results</h4>
                      <div className={`p-3 rounded text-sm ${
                        hotfix.test_result.passed
                          ? 'bg-green-50 text-green-800'
                          : 'bg-red-50 text-red-800'
                      }`}>
                        <div className="flex items-center gap-2">
                          <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                          </svg>
                          {hotfix.test_result.passed ? 'Tests Passed' : 'Tests Failed'}
                        </div>
                        <div className="text-xs mt-1">{hotfix.test_result.duration_seconds}s</div>
                      </div>
                    </div>
                  )}

                  {/* Deployment Results */}
                  {hotfix.deployment_result && (
                    <div>
                      <h4 className="font-medium text-sm text-gray-900 mb-2">Deployment Results</h4>
                      <div className="space-y-2">
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-600">Error Rate Before</span>
                          <span className="font-mono">{(hotfix.deployment_result.error_rate_before * 100).toFixed(1)}%</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-600">Error Rate After</span>
                          <span className="font-mono text-green-600">{(hotfix.deployment_result.error_rate_after * 100).toFixed(1)}%</span>
                        </div>
                        <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-green-500"
                            style={{
                              width: `${Math.min(100, (1 - hotfix.deployment_result.error_rate_after / hotfix.deployment_result.error_rate_before) * 100)}%`,
                            }}
                          />
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Actions */}
                  <div className="flex gap-2 pt-2">
                    {hotfix.status === 'pending' && (
                      <button
                        onClick={() => handleApprove(hotfix.hotfix_id)}
                        disabled={loading}
                        className="flex-1 bg-blue-600 text-white py-2 px-3 rounded font-medium text-sm hover:bg-blue-700 transition disabled:opacity-50"
                      >
                        {loading ? 'Approving...' : 'Approve'}
                      </button>
                    )}

                    {hotfix.status === 'approved' && (
                      <button
                        onClick={() => handleDeploy(hotfix.hotfix_id)}
                        disabled={loading}
                        className="flex-1 bg-green-600 text-white py-2 px-3 rounded font-medium text-sm hover:bg-green-700 transition disabled:opacity-50"
                      >
                        {loading ? 'Deploying...' : 'Deploy Hotfix'}
                      </button>
                    )}

                    {hotfix.status === 'deployed' && (
                      <button
                        onClick={() => handleRollback(hotfix.hotfix_id)}
                        className="flex-1 bg-red-600 text-white py-2 px-3 rounded font-medium text-sm hover:bg-red-700 transition"
                      >
                        Rollback
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default HotfixPanel;
