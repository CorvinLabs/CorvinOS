/**
 * Preset Switcher — Installation profile selector (Phase 5, ADR-0287)
 *
 * Shows: Minimal / Standard / Advanced
 * Allows: switch presets (requires restart)
 * Persists: to tenant.corvin.yaml spec.preset
 */
import { useEffect, useState } from 'react';

interface PresetInfo {
  id: string;
  label: string;
  description: string;
}

const PRESETS: PresetInfo[] = [
  {
    id: 'minimal',
    label: 'Minimal',
    description: 'Core only; features are opt-in via YAML or Settings.',
  },
  {
    id: 'standard',
    label: 'Standard',
    description: 'Recommended for most users. Stable & production features enabled.',
  },
  {
    id: 'advanced',
    label: 'Advanced',
    description: 'For explorers. Includes beta features; alpha remains opt-in.',
  },
];

export function PresetSwitcher() {
  const [currentPreset, setCurrentPreset] = useState('standard');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Fetch current preset from API
    const fetchPreset = async () => {
      try {
        setLoading(true);
        const res = await fetch('/v1/console/api/feature-status/preset');
        if (!res.ok) throw new Error('Failed to fetch preset');
        const data = await res.json();
        setCurrentPreset(data.preset || 'standard');
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Unknown error');
        // Default to standard if fetch fails
        setCurrentPreset('standard');
      } finally {
        setLoading(false);
      }
    };

    fetchPreset();
  }, []);

  const handlePresetChange = async (presetId: string) => {
    if (presetId === currentPreset) return;

    try {
      const res = await fetch('/v1/console/api/feature-status/preset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset: presetId }),
      });

      if (!res.ok) throw new Error('Failed to update preset');

      setCurrentPreset(presetId);
      alert(`Preset changed to ${presetId}. Please restart CorvinOS for changes to take effect.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update preset');
    }
  };

  if (loading) return <div>Loading preset...</div>;

  return (
    <div style={{ padding: '16px', borderRadius: '8px', border: '1px solid #e5e7eb' }}>
      <h3 style={{ marginTop: 0 }}>Installation Preset</h3>
      <p style={{ fontSize: '14px', color: '#6b7280' }}>
        Choose which feature tiers auto-enable for new users.
      </p>

      {error && (
        <div style={{ padding: '8px', background: '#fee2e2', color: '#dc2626', borderRadius: '4px', marginBottom: '12px' }}>
          {error}
        </div>
      )}

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        {PRESETS.map((preset) => (
          <button
            key={preset.id}
            onClick={() => handlePresetChange(preset.id)}
            style={{
              padding: '12px 16px',
              borderRadius: '6px',
              border: currentPreset === preset.id ? '2px solid #059669' : '1px solid #d1d5db',
              background: currentPreset === preset.id ? '#ecfdf5' : '#f9fafb',
              color: currentPreset === preset.id ? '#059669' : '#111827',
              fontWeight: currentPreset === preset.id ? 'bold' : 'normal',
              cursor: 'pointer',
              fontSize: '14px',
            }}
          >
            {preset.label} {currentPreset === preset.id && '✓'}
          </button>
        ))}
      </div>

      <div style={{ marginTop: '16px', display: 'grid', gap: '8px' }}>
        {PRESETS.map((preset) => (
          <div key={preset.id} style={{ fontSize: '13px', color: '#6b7280' }}>
            <strong>{preset.label}:</strong> {preset.description}
          </div>
        ))}
      </div>
    </div>
  );
}

export default PresetSwitcher;
