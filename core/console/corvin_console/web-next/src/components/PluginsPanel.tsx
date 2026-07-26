/**
 * Plugins Settings Panel Component
 * 
 * Displays installed plugins with enable/disable toggles
 * Allows updating plugin settings
 */

import React, { useState } from 'react';
import { usePlugins, Plugin } from '../hooks/usePlugins';

interface PluginCardProps {
  plugin: Plugin;
  onEnable: (id: string) => Promise<void>;
  onDisable: (id: string) => Promise<void>;
  onConfigChange: (id: string, settings: Record<string, unknown>) => Promise<void>;
}

/**
 * Card for a single plugin
 */
function PluginCard({ plugin, onEnable, onDisable, onConfigChange }: PluginCardProps) {
  const [loading, setLoading] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const handleToggle = async () => {
    try {
      setLoading(true);
      if (plugin.enabled) {
        await onDisable(plugin.id);
      } else {
        await onEnable(plugin.id);
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSettingChange = async (key: string, value: unknown) => {
    try {
      setLoading(true);
      const newSettings = {
        ...plugin.settings,
        [key]: value,
      };
      await onConfigChange(plugin.id, newSettings);
    } finally {
      setLoading(false);
    }
  };

  const tierBadgeColor = {
    a: 'bg-blue-100 text-blue-800',
    b: 'bg-green-100 text-green-800',
    c: 'bg-yellow-100 text-yellow-800',
  }[plugin.tier];

  const piiColor = {
    none: 'text-green-600',
    low: 'text-yellow-600',
    medium: 'text-orange-600',
    high: 'text-red-600',
  }[plugin.pii_risk];

  return (
    <div className="border rounded-lg p-4 mb-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-lg">{plugin.name}</h3>
            <span className={`text-xs px-2 py-1 rounded ${tierBadgeColor}`}>
              Tier {plugin.tier.toUpperCase()}
            </span>
          </div>
          <p className="text-sm text-gray-500">v{plugin.version}</p>
        </div>

        {/* Enable/Disable Toggle */}
        <div className="flex items-center gap-2">
          <span className={`text-sm font-medium ${piiColor}`}>
            PII: {plugin.pii_risk}
          </span>
          <label className="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              className="sr-only peer"
              checked={plugin.enabled}
              onChange={handleToggle}
              disabled={loading}
            />
            <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600" />
          </label>
        </div>
      </div>

      {/* Settings (Collapsible) */}
      {plugin.settings_schema && Object.keys(plugin.settings_schema).length > 0 && (
        <div className="mt-4 pt-4 border-t">
          <button
            onClick={() => setSettingsOpen(!settingsOpen)}
            className="text-sm font-medium text-blue-600 hover:text-blue-800"
          >
            {settingsOpen ? '▼' : '▶'} Settings
          </button>

          {settingsOpen && (
            <div className="mt-3 space-y-2">
              {Object.entries(plugin.settings).map(([key, value]) => (
                <div key={key}>
                  <label className="text-sm font-medium text-gray-700">
                    {key}
                  </label>
                  <input
                    type="text"
                    value={String(value)}
                    onChange={(e) => handleSettingChange(key, e.target.value)}
                    disabled={loading}
                    className="w-full px-2 py-1 border rounded text-sm"
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Main Plugins Panel
 */
export function PluginsPanel() {
  const { plugins, loading, error, enable, disable, updateConfig, refetch } = usePlugins();

  if (loading) {
    return <div className="p-4">Loading plugins...</div>;
  }

  if (error) {
    return (
      <div className="p-4 bg-red-50 border border-red-200 rounded">
        <p className="text-red-800">Error: {error}</p>
        <button
          onClick={refetch}
          className="mt-2 px-3 py-1 bg-red-600 text-white text-sm rounded hover:bg-red-700"
        >
          Retry
        </button>
      </div>
    );
  }

  if (plugins.length === 0) {
    return (
      <div className="p-4 bg-gray-50 border border-gray-200 rounded">
        <p className="text-gray-600">No plugins installed yet</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-bold">Installed Plugins</h2>
        <button
          onClick={refetch}
          className="px-3 py-1 bg-blue-600 text-white text-sm rounded hover:bg-blue-700"
        >
          Refresh
        </button>
      </div>

      <div>
        {plugins.map((plugin) => (
          <PluginCard
            key={plugin.id}
            plugin={plugin}
            onEnable={enable}
            onDisable={disable}
            onConfigChange={updateConfig}
          />
        ))}
      </div>
    </div>
  );
}
