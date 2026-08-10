/**
 * Feature Status Dashboard — show tier, metrics, adoption (Phase 5, ADR-0288)
 *
 * Shows: per-feature tier badge, error rate, days in tier, adoption %
 * Filters: by tier or search
 * Integrates: with telemetry real-time metrics
 */
import { useEffect, useState } from 'react';

interface FeatureStatus {
  flag_id: string;
  release_tier: 'alpha' | 'beta' | 'stable' | 'production';
  error_rate_24h: number;
  invocation_count_24h: number;
  days_since_last_error: number | null;
  status: 'active' | 'degraded' | 'failed';
}

const TIER_COLORS: Record<string, { bg: string; text: string }> = {
  alpha: { bg: '#fee2e2', text: '#dc2626' },
  beta: { bg: '#fef3c7', text: '#d97706' },
  stable: { bg: '#dcfce7', text: '#16a34a' },
  production: { bg: '#cffafe', text: '#0369a1' },
};

export function FeatureStatusDashboard() {
  const [features, setFeatures] = useState<FeatureStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchText, setSearchText] = useState('');
  const [filterTier, setFilterTier] = useState<string | null>(null);

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        setLoading(true);
        const res = await fetch('/v1/console/api/feature-status');
        if (!res.ok) throw new Error('Failed to fetch status');
        const data = await res.json();
        setFeatures(data.flags_enabled || []);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchStatus();
    // Refresh every 5 minutes
    const interval = setInterval(fetchStatus, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  const filtered = features.filter((f) => {
    const matchesSearch = f.flag_id.includes(searchText.toLowerCase());
    const matchesTier = !filterTier || f.release_tier === filterTier;
    return matchesSearch && matchesTier;
  });

  if (loading) return <div>Loading features...</div>;

  return (
    <div style={{ padding: '16px' }}>
      <h2 style={{ marginTop: 0 }}>Feature Status</h2>

      {error && (
        <div style={{ padding: '12px', background: '#fee2e2', color: '#dc2626', borderRadius: '6px', marginBottom: '16px' }}>
          {error}
        </div>
      )}

      {/* Search & Filter */}
      <div style={{ display: 'flex', gap: '12px', marginBottom: '16px', flexWrap: 'wrap' }}>
        <input
          type="text"
          placeholder="Search flag..."
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          style={{
            padding: '8px 12px',
            borderRadius: '6px',
            border: '1px solid #d1d5db',
            fontSize: '14px',
          }}
        />

        <select
          value={filterTier || ''}
          onChange={(e) => setFilterTier(e.target.value || null)}
          style={{
            padding: '8px 12px',
            borderRadius: '6px',
            border: '1px solid #d1d5db',
            fontSize: '14px',
          }}
        >
          <option value="">All Tiers</option>
          <option value="alpha">Alpha</option>
          <option value="beta">Beta</option>
          <option value="stable">Stable</option>
          <option value="production">Production</option>
        </select>
      </div>

      {/* Features Grid */}
      <div style={{ display: 'grid', gap: '12px' }}>
        {filtered.length === 0 ? (
          <p style={{ color: '#6b7280' }}>No features match your search.</p>
        ) : (
          filtered.map((feature) => {
            const colors = TIER_COLORS[feature.release_tier];
            return (
              <div
                key={feature.flag_id}
                style={{
                  padding: '12px',
                  borderRadius: '6px',
                  border: '1px solid #e5e7eb',
                  display: 'grid',
                  gridTemplateColumns: '1fr auto',
                  gap: '16px',
                }}
              >
                <div>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '4px' }}>
                    <strong>{feature.flag_id}</strong>
                    <span
                      style={{
                        padding: '2px 8px',
                        borderRadius: '4px',
                        background: colors.bg,
                        color: colors.text,
                        fontSize: '12px',
                        fontWeight: 'bold',
                      }}
                    >
                      {feature.release_tier}
                    </span>
                  </div>
                  <div style={{ fontSize: '13px', color: '#6b7280' }}>
                    Error Rate: {(feature.error_rate_24h * 100).toFixed(2)}% • Invocations: {feature.invocation_count_24h}
                  </div>
                </div>

                <div
                  style={{
                    textAlign: 'right',
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'center',
                  }}
                >
                  <div
                    style={{
                      fontSize: '12px',
                      fontWeight: 'bold',
                      color: feature.status === 'active' ? '#059669' : '#dc2626',
                    }}
                  >
                    {feature.status === 'active' ? '✓ Active' : '⚠ ' + feature.status}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Summary */}
      <div style={{ marginTop: '24px', fontSize: '13px', color: '#6b7280' }}>
        <strong>{features.length} total features:</strong>
        {' '}
        {features.filter((f) => f.release_tier === 'alpha').length} alpha,
        {' '}
        {features.filter((f) => f.release_tier === 'beta').length} beta,
        {' '}
        {features.filter((f) => f.release_tier === 'stable').length} stable,
        {' '}
        {features.filter((f) => f.release_tier === 'production').length} production
      </div>
    </div>
  );
}

export default FeatureStatusDashboard;
