/**
 * Settings Page — Feature Flags Management
 * Allows users to enable/disable features in the Console
 */

import React, { useState, useEffect } from 'react';
import './Settings.css';

interface Feature {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
}

interface WhitelistResponse {
  whitelist: string[];
  mode: string;
  total_features: number;
}

export const Settings: React.FC = () => {
  const [features, setFeatures] = useState<Feature[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const featureRegistry: Record<string, { name: string; description: string }> = {
    'token_metrics_v2': { name: 'Token Metrics v2', description: 'New token saving metrics and analysis' },
    'context_pipeline_graphs': { name: 'Context Pipeline Graphs', description: 'Visual graphs for context pipeline stages' },
    'learning_nodes': { name: 'Learning Nodes', description: 'TreeOfThoughts and learning node visualization' },
    'cross_device_sync': { name: 'Cross-Device Sync', description: 'Synchronize learning across devices' },
    'advanced_analytics': { name: 'Advanced Analytics', description: 'Extended analytics and reporting' },
  };

  useEffect(() => {
    loadFeatures();
  }, []);

  const loadFeatures = async () => {
    try {
      setLoading(true);
      const res = await fetch('/v1/console/features/whitelist');
      if (!res.ok) throw new Error('Failed to fetch features');

      const data: WhitelistResponse = await res.json();

      const featureList = Object.entries(featureRegistry).map(([id, info]) => ({
        id,
        name: info.name,
        description: info.description,
        enabled: data.whitelist.includes(id),
      }));

      setFeatures(featureList);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load features');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const toggleFeature = async (featureId: string, enabled: boolean) => {
    try {
      setSaving(true);
      const res = await fetch('/v1/console/features/toggle', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '',
        },
        body: JSON.stringify({ feature_id: featureId, enabled: !enabled }),
      });

      if (!res.ok) throw new Error('Failed to toggle feature');

      setFeatures(features.map(f =>
        f.id === featureId ? { ...f, enabled: !f.enabled } : f
      ));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to toggle feature');
      console.error(err);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="settings-loading">⚙️ Loading settings...</div>;
  }

  return (
    <div className="settings-container">
      <header className="settings-header">
        <h1>⚙️ Settings</h1>
        <p>Configure features and preferences for your CorvinOS Console</p>
      </header>

      {error && (
        <div className="settings-error">
          ⚠️ {error}
        </div>
      )}

      <section className="settings-section">
        <h2>Feature Flags</h2>
        <p className="section-description">
          Enable or disable experimental and advanced features. Changes take effect immediately.
        </p>

        <div className="features-list">
          {features.map(feature => (
            <div key={feature.id} className="feature-item">
              <div className="feature-info">
                <h3>{feature.name}</h3>
                <p>{feature.description}</p>
                <code className="feature-id">{feature.id}</code>
              </div>
              <button
                className={`toggle-button ${feature.enabled ? 'enabled' : 'disabled'}`}
                onClick={() => toggleFeature(feature.id, feature.enabled)}
                disabled={saving}
                title={feature.enabled ? 'Click to disable' : 'Click to enable'}
              >
                {feature.enabled ? '✓ On' : '✕ Off'}
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="settings-section">
        <h2>About</h2>
        <div className="about-info">
          <p><strong>CorvinOS Console</strong> v0.2-rc1</p>
          <p>Operator Console for Vibe Engineering System</p>
        </div>
      </section>
    </div>
  );
};

export default Settings;
