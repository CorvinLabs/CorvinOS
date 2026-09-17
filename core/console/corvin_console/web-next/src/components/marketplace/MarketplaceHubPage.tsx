/**
 * Marketplace Hub Page — Unified Discovery + Installation
 * Session 5 Milestone F: API Wiring + E2E Tests
 *
 * Integrates:
 * - MarketplaceCards (5 card types)
 * - MarketplaceSearch (query + filters)
 * - Backend APIs (/marketplace/plugins/available, /marketplace/plugins/installed)
 *
 * NOTE: Uses Tailwind CSS (consistent with project design system).
 * Refactored from Mantine to reduce dependencies.
 */

import React, { useState, useEffect } from 'react';
import { AlertCircle } from 'lucide-react';
import {
  PluginCard,
  SkillCard,
  DatasetCard,
  ServiceCard,
  TemplateCard,
} from './MarketplaceCards';
import { MarketplaceSearch } from './MarketplaceSearch';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

export interface Plugin {
  id: string;
  name: string;
  version: string;
  description: string;
  tier: string;
  category: string;
  icon?: string;
  installCount?: number;
}

export interface SearchResult {
  id: string;
  name: string;
  type: 'plugin' | 'skill' | 'dataset' | 'service' | 'template';
  description?: string;
  updated?: string;
  rating?: number;
  status?: 'active' | 'inactive';
}

// ============================================================================
// MARKETPLACE HUB PAGE
// ============================================================================

export const MarketplaceHubPage: React.FC = () => {
  // State management
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [installedPlugins, setInstalledPlugins] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'discover' | 'installed' | 'search'>('discover');
  const [installing, setInstalling] = useState<Set<string>>(new Set());

  // ============================================================================
  // API CALLS
  // ============================================================================

  const fetchAvailablePlugins = async () => {
    try {
      const response = await fetch('/v1/marketplace/plugins/available');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setPlugins(data.plugins || []);
    } catch (err) {
      setError(`Failed to fetch plugins: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const fetchInstalledPlugins = async () => {
    try {
      const response = await fetch('/v1/marketplace/plugins/installed');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const installed = new Set<string>(data.plugins.map((p: any) => p.id));
      setInstalledPlugins(installed);
    } catch (err) {
      console.warn('Failed to fetch installed plugins:', err);
      setInstalledPlugins(new Set());
    }
  };

  // Load data on mount
  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      try {
        await Promise.all([fetchAvailablePlugins(), fetchInstalledPlugins()]);
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, []);

  // ============================================================================
  // EVENT HANDLERS
  // ============================================================================

  const handleInstallPlugin = async (pluginId: string) => {
    setInstalling((prev) => new Set([...prev, pluginId]));
    try {
      const response = await fetch(`/v1/marketplace/plugins/${pluginId}/install`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data.status === 'installing' || data.status === 'installed') {
        setInstalledPlugins((prev) => new Set([...prev, pluginId]));
      }
    } catch (err) {
      setError(
        `Failed to install plugin: ${err instanceof Error ? err.message : String(err)}`
      );
    } finally {
      setInstalling((prev) => {
        const updated = new Set(prev);
        updated.delete(pluginId);
        return updated;
      });
    }
  };

  const handleSearchResult = (result: SearchResult) => {
    console.log('Search result selected:', result);
    // Could open a detail modal or navigate to a detail page
  };

  // ============================================================================
  // RENDER
  // ============================================================================

  if (loading) {
    return (
      <div className="w-full flex items-center justify-center py-12">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4"></div>
          <p className="text-gray-600">Loading marketplace…</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full px-4 py-8">
        <div className="max-w-4xl mx-auto border border-red-200 rounded-lg p-4 bg-red-50">
          <div className="flex gap-3">
            <AlertCircle className="text-red-600 flex-shrink-0" size={20} />
            <div>
              <h3 className="font-semibold text-red-900">Error</h3>
              <p className="text-red-800 text-sm mt-1">{error}</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Convert plugins to search results for search component
  const searchResults: SearchResult[] = plugins.map((p) => ({
    id: p.id,
    name: p.name,
    type: 'plugin',
    description: p.description,
    status: installedPlugins.has(p.id) ? 'active' : 'inactive',
  }));

  return (
    <div className="w-full" data-testid="marketplace-hub-page">
      {/* Tabs Navigation */}
      <div className="border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4">
          <div className="flex gap-8">
            {['discover', 'installed', 'search'].map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab as 'discover' | 'installed' | 'search')}
                className={`py-4 px-1 border-b-2 font-medium text-sm capitalize transition ${
                  activeTab === tab
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-gray-600 hover:text-gray-900'
                }`}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Tab Content */}
      <div className="max-w-7xl mx-auto px-4 py-8">
        {/* ============================================================================
          TAB 1: DISCOVER
          ============================================================================ */}
        {activeTab === 'discover' && (
          <div className="space-y-6">
            <div>
              <h2 className="text-2xl font-bold text-gray-900">Featured Plugins</h2>
              <p className="text-gray-600 mt-1">Discover and install plugins, skills, datasets, and templates</p>
            </div>

            {/* Card Grid — responsive */}
            <div
              data-testid="plugin-cards-grid"
              className="grid gap-4 grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4"
            >
              {plugins.slice(0, 6).map((plugin, idx) => {
                const isInstalled = installedPlugins.has(plugin.id);
                const isInstalling = installing.has(plugin.id);

                // Cycle through card types for demo purposes
                const cardTypes = [
                  'plugin',
                  'skill',
                  'dataset',
                  'service',
                  'template',
                ];
                const cardType = cardTypes[idx % cardTypes.length];

                return (
                  <div key={plugin.id} data-testid={`card-${plugin.id}`} className="relative">
                    {isInstalling && (
                      <div className="absolute inset-0 bg-black/10 rounded-lg flex items-center justify-center z-10">
                        <div className="inline-block animate-spin rounded-full h-6 w-6 border-2 border-blue-600 border-t-transparent"></div>
                      </div>
                    )}

                    {cardType === 'plugin' && (
                      <PluginCard
                        id={plugin.id}
                        name={plugin.name}
                        version={plugin.version}
                        description={plugin.description}
                        installCount={Math.floor(Math.random() * 100)}
                        onInstall={() => handleInstallPlugin(plugin.id)}
                      />
                    )}
                    {cardType === 'skill' && (
                      <SkillCard
                        id={plugin.id}
                        name={plugin.name}
                        confidenceScore={Math.floor(Math.random() * 100)}
                        description={plugin.description}
                        onInstall={() => handleInstallPlugin(plugin.id)}
                      />
                    )}
                    {cardType === 'dataset' && (
                      <DatasetCard
                        id={plugin.id}
                        name={plugin.name}
                        rowCount={Math.floor(Math.random() * 10000)}
                        description={plugin.description}
                        onDownload={() => handleInstallPlugin(plugin.id)}
                      />
                    )}
                    {cardType === 'service' && (
                      <ServiceCard
                        id={plugin.id}
                        name={plugin.name}
                        description={plugin.description}
                        healthStatus={isInstalled ? 'active' : 'inactive'}
                      />
                    )}
                    {cardType === 'template' && (
                      <TemplateCard
                        id={plugin.id}
                        name={plugin.name}
                        category={plugin.category}
                        description={plugin.description}
                        onUse={() => handleInstallPlugin(plugin.id)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ============================================================================
          TAB 2: INSTALLED
          ============================================================================ */}
        {activeTab === 'installed' && (
          <div className="space-y-6">
            <div>
              <h2 className="text-2xl font-bold text-gray-900">Installed Plugins</h2>
              <p className="text-gray-600 mt-1">{installedPlugins.size} plugin(s) installed</p>
            </div>

            {installedPlugins.size > 0 ? (
              <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
                {plugins
                  .filter((p) => installedPlugins.has(p.id))
                  .map((plugin) => (
                    <div key={plugin.id}>
                      <PluginCard
                        id={plugin.id}
                        name={plugin.name}
                        version={plugin.version}
                        description={plugin.description}
                        onInstall={() => console.log('Already installed:', plugin.id)}
                      />
                    </div>
                  ))}
              </div>
            ) : (
              <div className="border border-blue-200 rounded-lg p-4 bg-blue-50">
                <p className="text-blue-900">No plugins installed yet. Explore the Discover tab to get started!</p>
              </div>
            )}
          </div>
        )}

        {/* ============================================================================
          TAB 3: SEARCH
          ============================================================================ */}
        {activeTab === 'search' && (
          <MarketplaceSearch
            data={searchResults}
            onResultSelect={handleSearchResult}
          />
        )}
      </div>
    </div>
  );
};

export default MarketplaceHubPage;
