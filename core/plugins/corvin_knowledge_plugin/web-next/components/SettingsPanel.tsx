/**
 * Corvin-Knowledge Settings Panel
 * Configure repository path, remote URL, sync settings, consistency levels
 */

import React, { useState, useEffect } from 'react';

interface SettingsConfig {
  repo_path: string;
  remote_url: string;
  auto_sync_on_query: boolean;
  consistency_level: 'strict' | 'warn' | 'ignore';
}

interface SettingsPanelProps {
  initialConfig?: Partial<SettingsConfig>;
  onSave?: (config: SettingsConfig) => void;
  onSync?: (syncType: 'pull' | 'push' | 'both') => void;
  syncStatus?: 'idle' | 'syncing' | 'success' | 'error';
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  initialConfig,
  onSave,
  onSync,
  syncStatus = 'idle',
}) => {
  const [config, setConfig] = useState<SettingsConfig>({
    repo_path: initialConfig?.repo_path || '~/.corvin-knowledge/',
    remote_url:
      initialConfig?.remote_url ||
      'https://github.com/CorvinLabs/Corvin-Knowledge.git',
    auto_sync_on_query: initialConfig?.auto_sync_on_query ?? true,
    consistency_level: initialConfig?.consistency_level || 'warn',
  });

  const [isDirty, setIsDirty] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const handleChange = (field: keyof SettingsConfig, value: any) => {
    setConfig((prev) => ({
      ...prev,
      [field]: value,
    }));
    setIsDirty(true);
  };

  const handleSave = () => {
    onSave?.(config);
    setIsDirty(false);
  };

  return (
    <div style={{ maxWidth: '600px', padding: '20px' }}>
      <style>{`
        .settings-group {
          margin-bottom: 24px;
          padding-bottom: 20px;
          border-bottom: 1px solid #e0e0e0;
        }
        .settings-group:last-child {
          border-bottom: none;
        }

        .setting-row {
          margin-bottom: 16px;
        }

        .setting-label {
          display: block;
          font-weight: 600;
          font-size: 14px;
          margin-bottom: 6px;
          color: #333;
        }

        .setting-input {
          width: 100%;
          padding: 8px 12px;
          border: 1px solid #ddd;
          border-radius: 6px;
          font-size: 14px;
          font-family: monospace;
          box-sizing: border-box;
        }

        .setting-input:focus {
          outline: none;
          border-color: #2196F3;
          box-shadow: 0 0 0 3px rgba(33, 150, 243, 0.1);
        }

        .setting-description {
          font-size: 12px;
          color: #666;
          margin-top: 4px;
        }

        .consistency-option {
          display: flex;
          align-items: center;
          padding: 8px;
          margin: 4px 0;
          border-radius: 4px;
          cursor: pointer;
          background-color: #f5f5f5;
          transition: all 0.2s;
        }

        .consistency-option:hover {
          background-color: #e8e8e8;
        }

        .consistency-option input[type="radio"] {
          margin-right: 8px;
          cursor: pointer;
        }

        .consistency-option.selected {
          background-color: #e3f2fd;
        }

        .button-group {
          display: flex;
          gap: 8px;
          margin-top: 20px;
        }

        .btn {
          padding: 10px 16px;
          border: none;
          border-radius: 6px;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s;
        }

        .btn-primary {
          background-color: #2196F3;
          color: white;
        }

        .btn-primary:hover:not(:disabled) {
          background-color: #1976D2;
        }

        .btn-secondary {
          background-color: #f5f5f5;
          color: #333;
          border: 1px solid #ddd;
        }

        .btn-secondary:hover:not(:disabled) {
          background-color: #e8e8e8;
        }

        .btn-sync {
          background-color: #4CAF50;
          color: white;
        }

        .btn-sync:hover:not(:disabled) {
          background-color: #388E3C;
        }

        .btn:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .status-message {
          padding: 12px;
          border-radius: 6px;
          margin-bottom: 16px;
          font-size: 13px;
        }

        .status-success {
          background-color: #c8e6c9;
          color: #2e7d32;
          border: 1px solid #81c784;
        }

        .status-error {
          background-color: #ffcdd2;
          color: #c62828;
          border: 1px solid #ef5350;
        }

        .status-syncing {
          background-color: #fff3cd;
          color: #856404;
          border: 1px solid #ffc107;
        }

        .section-header {
          font-size: 16px;
          font-weight: 600;
          margin-bottom: 16px;
          color: #333;
        }

        .toggle-switch {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .toggle-switch input[type="checkbox"] {
          cursor: pointer;
        }

        .advanced-toggle {
          color: #2196F3;
          cursor: pointer;
          font-size: 13px;
          text-decoration: underline;
        }
      `}</style>

      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ margin: '0 0 8px 0', fontSize: '20px' }}>
          Knowledge Graph Settings
        </h2>
        <p style={{ margin: '0', color: '#666', fontSize: '13px' }}>
          Configure repository and sync preferences
        </p>
      </div>

      {/* Status Messages */}
      {syncStatus === 'success' && (
        <div className="status-message status-success">
          ✓ Sync completed successfully
        </div>
      )}
      {syncStatus === 'error' && (
        <div className="status-message status-error">
          ✗ Sync failed. Check your configuration.
        </div>
      )}
      {syncStatus === 'syncing' && (
        <div className="status-message status-syncing">
          ⟳ Syncing... Please wait
        </div>
      )}

      {/* Repository Settings */}
      <div className="settings-group">
        <div className="section-header">Repository</div>

        <div className="setting-row">
          <label className="setting-label">Repository Path</label>
          <input
            type="text"
            className="setting-input"
            value={config.repo_path}
            onChange={(e) => handleChange('repo_path', e.target.value)}
            placeholder="~/.corvin-knowledge/"
          />
          <div className="setting-description">
            Local directory where the knowledge repository is stored
          </div>
        </div>

        <div className="setting-row">
          <label className="setting-label">Remote URL</label>
          <input
            type="text"
            className="setting-input"
            value={config.remote_url}
            onChange={(e) => handleChange('remote_url', e.target.value)}
            placeholder="https://github.com/CorvinLabs/Corvin-Knowledge.git"
          />
          <div className="setting-description">
            Git remote URL (GitHub, GitLab, etc.)
          </div>
        </div>
      </div>

      {/* Sync Settings */}
      <div className="settings-group">
        <div className="section-header">Sync Behavior</div>

        <div className="setting-row toggle-switch">
          <input
            type="checkbox"
            id="auto-sync"
            checked={config.auto_sync_on_query}
            onChange={(e) => handleChange('auto_sync_on_query', e.target.checked)}
          />
          <label htmlFor="auto-sync" style={{ margin: '0', cursor: 'pointer' }}>
            Auto-sync before queries (pulls latest changes)
          </label>
        </div>

        <div className="setting-row">
          <label className="setting-label">Consistency Level</label>
          <div>
            {(['strict', 'warn', 'ignore'] as const).map((level) => (
              <div
                key={level}
                className={`consistency-option ${
                  config.consistency_level === level ? 'selected' : ''
                }`}
              >
                <input
                  type="radio"
                  name="consistency"
                  value={level}
                  checked={config.consistency_level === level}
                  onChange={(e) =>
                    handleChange('consistency_level', e.target.value as any)
                  }
                />
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: '600', fontSize: '13px' }}>
                    {level.charAt(0).toUpperCase() + level.slice(1)}
                  </div>
                  <div style={{ fontSize: '12px', color: '#666' }}>
                    {level === 'strict' && 'Abort sync on any validation error'}
                    {level === 'warn' && 'Log errors but continue sync'}
                    {level === 'ignore' && 'Skip validation (development only)'}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Advanced Settings */}
      <div className="settings-group">
        <div
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="advanced-toggle"
        >
          {showAdvanced ? '▼ Advanced' : '▶ Advanced'}
        </div>

        {showAdvanced && (
          <div style={{ marginTop: '12px' }}>
            <div
              style={{
                padding: '12px',
                backgroundColor: '#f5f5f5',
                borderRadius: '6px',
                fontSize: '12px',
                fontFamily: 'monospace',
                color: '#666',
              }}
            >
              <p style={{ margin: '0 0 8px 0' }}>
                <strong>Configuration File:</strong>
              </p>
              <p style={{ margin: '0 0 8px 0' }}>
                ~/.claude/plugins/corvin-knowledge.json
              </p>
              <p style={{ margin: '0' }}>
                Advanced users can edit this file directly. Settings will reload
                on next sync.
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Sync Actions */}
      <div className="settings-group">
        <div className="section-header">Sync Actions</div>
        <div className="button-group">
          <button
            className="btn btn-sync"
            onClick={() => onSync?.('pull')}
            disabled={syncStatus === 'syncing'}
          >
            {syncStatus === 'syncing' ? '⟳ Pulling...' : '↓ Pull'}
          </button>
          <button
            className="btn btn-sync"
            onClick={() => onSync?.('push')}
            disabled={syncStatus === 'syncing'}
          >
            {syncStatus === 'syncing' ? '⟳ Pushing...' : '↑ Push'}
          </button>
          <button
            className="btn btn-sync"
            onClick={() => onSync?.('both')}
            disabled={syncStatus === 'syncing'}
          >
            {syncStatus === 'syncing' ? '⟳ Syncing...' : '↔ Sync'}
          </button>
        </div>
      </div>

      {/* Save/Cancel Buttons */}
      <div className="button-group">
        <button
          className="btn btn-primary"
          onClick={handleSave}
          disabled={!isDirty}
        >
          Save Settings
        </button>
        {isDirty && (
          <button
            className="btn btn-secondary"
            onClick={() => {
              setConfig(
                initialConfig as SettingsConfig || {
                  repo_path: '~/.corvin-knowledge/',
                  remote_url:
                    'https://github.com/CorvinLabs/Corvin-Knowledge.git',
                  auto_sync_on_query: true,
                  consistency_level: 'warn',
                }
              );
              setIsDirty(false);
            }}
          >
            Discard Changes
          </button>
        )}
      </div>
    </div>
  );
};

export default SettingsPanel;
