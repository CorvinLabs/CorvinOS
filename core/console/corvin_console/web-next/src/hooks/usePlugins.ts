/**
 * React Hook for Plugin Management
 * 
 * Handles plugin list, enable/disable, settings updates
 * Integrates with /api/plugins endpoints
 */

import { useState, useEffect, useCallback } from 'react';

export interface Plugin {
  id: string;
  version: string;
  name: string;
  enabled: boolean;
  tier: 'a' | 'b' | 'c';
  pii_risk: 'none' | 'low' | 'medium' | 'high';
  settings: Record<string, unknown>;
  settings_schema: Record<string, unknown>;
}

export interface PluginListResponse {
  plugins: Plugin[];
  total: number;
}

export interface UsePluginsReturn {
  plugins: Plugin[];
  loading: boolean;
  error: string | null;
  enable: (pluginId: string) => Promise<Plugin>;
  disable: (pluginId: string) => Promise<Plugin>;
  updateConfig: (pluginId: string, settings: Record<string, unknown>) => Promise<Plugin>;
  refetch: () => Promise<void>;
}

/**
 * Hook for managing plugins via REST API
 */
export function usePlugins(): UsePluginsReturn {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /**
   * Fetch all plugins
   */
  const fetchPlugins = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const response = await fetch('/api/plugins');
      if (!response.ok) {
        throw new Error(`Failed to fetch plugins: ${response.statusText}`);
      }

      const data: PluginListResponse = await response.json();
      setPlugins(data.plugins);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setError(message);
      console.error('Failed to fetch plugins:', message);
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Enable a plugin
   */
  const enable = useCallback(async (pluginId: string): Promise<Plugin> => {
    try {
      const response = await fetch(`/api/plugins/${pluginId}/enable`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });

      if (!response.ok) {
        throw new Error(`Failed to enable plugin: ${response.statusText}`);
      }

      const updatedPlugin: Plugin = await response.json();
      setPlugins((prev) =>
        prev.map((p) => (p.id === pluginId ? updatedPlugin : p))
      );
      return updatedPlugin;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setError(message);
      throw err;
    }
  }, []);

  /**
   * Disable a plugin
   */
  const disable = useCallback(async (pluginId: string): Promise<Plugin> => {
    try {
      const response = await fetch(`/api/plugins/${pluginId}/disable`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });

      if (!response.ok) {
        throw new Error(`Failed to disable plugin: ${response.statusText}`);
      }

      const updatedPlugin: Plugin = await response.json();
      setPlugins((prev) =>
        prev.map((p) => (p.id === pluginId ? updatedPlugin : p))
      );
      return updatedPlugin;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setError(message);
      throw err;
    }
  }, []);

  /**
   * Update plugin settings
   */
  const updateConfig = useCallback(
    async (pluginId: string, settings: Record<string, unknown>): Promise<Plugin> => {
      try {
        const response = await fetch(`/api/plugins/${pluginId}/config`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ settings }),
        });

        if (!response.ok) {
          throw new Error(`Failed to update config: ${response.statusText}`);
        }

        const updatedPlugin: Plugin = await response.json();
        setPlugins((prev) =>
          prev.map((p) => (p.id === pluginId ? updatedPlugin : p))
        );
        return updatedPlugin;
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error';
        setError(message);
        throw err;
      }
    },
    []
  );

  /**
   * Fetch on mount
   */
  useEffect(() => {
    fetchPlugins();
  }, [fetchPlugins]);

  return {
    plugins,
    loading,
    error,
    enable,
    disable,
    updateConfig,
    refetch: fetchPlugins,
  };
}
