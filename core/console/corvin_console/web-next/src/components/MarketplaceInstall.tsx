/**
 * Marketplace Install Dialog
 * 
 * Shows available plugins in marketplace with Install button
 */

import React, { useState } from 'react';

export interface MarketplacePlugin {
  id: string;
  name: string;
  version: string;
  description: string;
  marketplace_id: string;
  download_url: string;
  checksum: string;
  tier: 'a' | 'b' | 'c';
}

interface MarketplaceInstallProps {
  onInstallSuccess?: () => void;
}

// Mock marketplace data
const MOCK_MARKETPLACE: MarketplacePlugin[] = [
  {
    id: 'ai-code-review',
    name: 'AI Code Review',
    version: '2.0.1',
    description: 'Automated code review with Claude',
    marketplace_id: 'corvinlabs/ai-code-review/2.0.1',
    download_url: 'https://marketplace.corvinlabs.com/plugins/ai-code-review-2.0.1.zip',
    checksum: 'sha256:deadbeef',
    tier: 'b'
  },
  {
    id: 'postgres-tool',
    name: 'PostgreSQL Query Tool',
    version: '1.5.2',
    description: 'Execute SQL queries directly',
    marketplace_id: 'corvinlabs/postgres-tool/1.5.2',
    download_url: 'https://marketplace.corvinlabs.com/plugins/postgres-tool-1.5.2.zip',
    checksum: 'sha256:cafe1234',
    tier: 'b'
  },
];

/**
 * Plugin card in marketplace
 */
function MarketplacePluginCard({
  plugin,
  onInstall,
  loading
}: {
  plugin: MarketplacePlugin;
  onInstall: (p: MarketplacePlugin) => Promise<void>;
  loading: boolean;
}) {
  const [installing, setInstalling] = useState(false);

  const handleInstall = async () => {
    try {
      setInstalling(true);
      await onInstall(plugin);
    } finally {
      setInstalling(false);
    }
  };

  return (
    <div className="border rounded-lg p-4 mb-4">
      <div className="flex justify-between items-start">
        <div>
          <h3 className="font-semibold text-lg">{plugin.name}</h3>
          <p className="text-sm text-gray-600">{plugin.description}</p>
          <p className="text-xs text-gray-500 mt-2">v{plugin.version}</p>
        </div>
        <button
          onClick={handleInstall}
          disabled={installing || loading}
          className="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:bg-gray-400"
        >
          {installing ? 'Installing...' : 'Install'}
        </button>
      </div>
    </div>
  );
}

/**
 * Marketplace browser
 */
export function MarketplaceInstall({ onInstallSuccess }: MarketplaceInstallProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleInstall = async (plugin: MarketplacePlugin) => {
    try {
      setLoading(true);
      setError(null);

      const response = await fetch(
        `/api/plugins/${plugin.id}/install`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            marketplace_id: plugin.marketplace_id,
            download_url: plugin.download_url,
            checksum: plugin.checksum,
          }),
        }
      );

      if (!response.ok) {
        throw new Error(`Install failed: ${response.statusText}`);
      }

      onInstallSuccess?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setError(message);
      console.error('Install failed:', message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-bold mb-2">Marketplace</h2>
        <p className="text-gray-600 mb-4">
          Discover and install new plugins from our marketplace
        </p>
      </div>

      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded">
          <p className="text-red-800">{error}</p>
        </div>
      )}

      <div>
        {MOCK_MARKETPLACE.map((plugin) => (
          <MarketplacePluginCard
            key={plugin.id}
            plugin={plugin}
            onInstall={handleInstall}
            loading={loading}
          />
        ))}
      </div>
    </div>
  );
}
