/**
 * Main Knowledge Graph Panel for Corvin Console
 * Integrates graph visualization and settings management
 */

import React, { useState, useEffect, useCallback } from 'react';
import GraphVisualization from '../components/GraphVisualization';
import SettingsPanel from '../components/SettingsPanel';

interface PanelState {
  activeTab: 'graph' | 'settings';
  isLoading: boolean;
  error: string | null;
  syncStatus: 'idle' | 'syncing' | 'success' | 'error';
}

export const GraphPanel: React.FC = () => {
  const [state, setState] = useState<PanelState>({
    activeTab: 'graph',
    isLoading: true,
    error: null,
    syncStatus: 'idle',
  });

  const [graphData, setGraphData] = useState({
    entities: [],
    relations: [],
  });

  const [config, setConfig] = useState({
    repo_path: '~/.corvin-knowledge/',
    remote_url: 'https://github.com/CorvinLabs/Corvin-Knowledge.git',
    auto_sync_on_query: true,
    consistency_level: 'warn' as const,
  });

  // Load configuration on mount
  useEffect(() => {
    const loadConfig = async () => {
      try {
        const response = await fetch('/v1/console/plugins/corvin-knowledge/config');
        if (response.ok) {
          const data = await response.json();
          setConfig(data);
        }
      } catch (error) {
        console.error('Failed to load config:', error);
      }
    };

    loadConfig();
  }, []);

  // Load graph data
  useEffect(() => {
    const loadGraphData = async () => {
      setState((prev) => ({ ...prev, isLoading: true, error: null }));
      try {
        const response = await fetch('/v1/console/plugins/corvin-knowledge/graph');
        if (response.ok) {
          const data = await response.json();
          setGraphData(data);
          setState((prev) => ({ ...prev, isLoading: false }));
        } else {
          throw new Error('Failed to load graph data');
        }
      } catch (error: any) {
        setState((prev) => ({
          ...prev,
          isLoading: false,
          error: error.message,
        }));
      }
    };

    if (state.activeTab === 'graph') {
      loadGraphData();
    }
  }, [state.activeTab]);

  const handleSaveConfig = useCallback(async (newConfig) => {
    try {
      const response = await fetch('/v1/console/plugins/corvin-knowledge/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newConfig),
      });
      if (response.ok) {
        setConfig(newConfig);
        setState((prev) => ({ ...prev, syncStatus: 'success' }));
        setTimeout(() => {
          setState((prev) => ({ ...prev, syncStatus: 'idle' }));
        }, 2000);
      } else {
        throw new Error('Failed to save config');
      }
    } catch (error: any) {
      setState((prev) => ({ ...prev, syncStatus: 'error' }));
    }
  }, []);

  const handleSync = useCallback(async (syncType: 'pull' | 'push' | 'both') => {
    setState((prev) => ({ ...prev, syncStatus: 'syncing' }));
    try {
      const response = await fetch('/v1/console/plugins/corvin-knowledge/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sync_type: syncType }),
      });
      if (response.ok) {
        // Reload graph data after sync
        const graphResponse = await fetch('/v1/console/plugins/corvin-knowledge/graph');
        if (graphResponse.ok) {
          const data = await graphResponse.json();
          setGraphData(data);
        }
        setState((prev) => ({ ...prev, syncStatus: 'success' }));
        setTimeout(() => {
          setState((prev) => ({ ...prev, syncStatus: 'idle' }));
        }, 2000);
      } else {
        throw new Error('Sync failed');
      }
    } catch (error) {
      setState((prev) => ({ ...prev, syncStatus: 'error' }));
    }
  }, []);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        backgroundColor: 'var(--color-background)',
        color: 'var(--color-text)',
      }}
    >
      {/* Header with Tabs */}
      <div
        style={{
          borderBottom: '1px solid var(--color-border)',
          padding: '16px 20px',
          display: 'flex',
          gap: '16px',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div style={{ display: 'flex', gap: '16px' }}>
          <TabButton
            label="Graph Explorer"
            icon="📊"
            active={state.activeTab === 'graph'}
            onClick={() => setState((prev) => ({ ...prev, activeTab: 'graph' }))}
          />
          <TabButton
            label="Settings"
            icon="⚙️"
            active={state.activeTab === 'settings'}
            onClick={() => setState((prev) => ({ ...prev, activeTab: 'settings' }))}
          />
        </div>
        <div style={{ fontSize: '12px', color: 'var(--color-text-secondary)' }}>
          {graphData.entities.length} entities • {graphData.relations.length} relations
        </div>
      </div>

      {/* Content Area */}
      <div
        style={{
          flex: 1,
          overflow: 'auto',
          padding: '20px',
        }}
      >
        {state.isLoading && state.activeTab === 'graph' ? (
          <LoadingState message="Loading knowledge graph..." />
        ) : state.error ? (
          <ErrorState message={state.error} />
        ) : state.activeTab === 'graph' ? (
          <GraphVisualization
            data={graphData}
            onEntitySelect={(entity) => console.log('Selected:', entity)}
            onNavigate={(entityId) => console.log('Navigate to:', entityId)}
          />
        ) : (
          <SettingsPanel
            initialConfig={config}
            onSave={handleSaveConfig}
            onSync={handleSync}
            syncStatus={state.syncStatus}
          />
        )}
      </div>

      {/* Footer */}
      <div
        style={{
          borderTop: '1px solid var(--color-border)',
          padding: '12px 20px',
          fontSize: '12px',
          color: 'var(--color-text-secondary)',
          display: 'flex',
          justifyContent: 'space-between',
        }}
      >
        <div>Corvin-Knowledge v1.0.0</div>
        <div>
          Repository:{' '}
          <code
            style={{
              backgroundColor: 'var(--color-background-secondary)',
              padding: '2px 6px',
              borderRadius: '3px',
            }}
          >
            {config.repo_path}
          </code>
        </div>
      </div>
    </div>
  );
};

// Tab Button Component
interface TabButtonProps {
  label: string;
  icon: string;
  active: boolean;
  onClick: () => void;
}

const TabButton: React.FC<TabButtonProps> = ({ label, icon, active, onClick }) => (
  <button
    onClick={onClick}
    style={{
      background: 'none',
      border: 'none',
      padding: '8px 12px',
      cursor: 'pointer',
      fontSize: '14px',
      fontWeight: active ? '600' : '400',
      color: active ? 'var(--color-primary)' : 'var(--color-text-secondary)',
      borderBottom: active ? '2px solid var(--color-primary)' : 'none',
      transition: 'all 0.2s',
    }}
  >
    {icon} {label}
  </button>
);

// Loading State Component
const LoadingState: React.FC<{ message: string }> = ({ message }) => (
  <div
    style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      height: '400px',
      gap: '12px',
      color: 'var(--color-text-secondary)',
    }}
  >
    <div style={{ animation: 'spin 1s linear infinite' }}>⟳</div>
    <div>{message}</div>
    <style>{`
      @keyframes spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
      }
    `}</style>
  </div>
);

// Error State Component
const ErrorState: React.FC<{ message: string }> = ({ message }) => (
  <div
    style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      height: '400px',
      padding: '20px',
      backgroundColor: 'var(--color-error-background)',
      borderRadius: '8px',
      color: 'var(--color-error-text)',
      textAlign: 'center',
    }}
  >
    <div>
      <div style={{ fontSize: '18px', marginBottom: '8px' }}>⚠️</div>
      <div>{message}</div>
      <div style={{ fontSize: '12px', marginTop: '12px', opacity: 0.7 }}>
        Check your settings and try again
      </div>
    </div>
  </div>
);

export default GraphPanel;
