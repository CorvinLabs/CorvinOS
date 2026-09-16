/**
 * Marketplace Hub Page — Unified Discovery + Installation
 * Session 5 Milestone F: API Wiring + E2E Tests
 *
 * Integrates:
 * - MarketplaceCards (5 card types)
 * - MarketplaceSearch (query + filters)
 * - Backend APIs (/marketplace/plugins/available, /marketplace/plugins/installed)
 */

import React, { useState, useEffect } from 'react';
import { Container, Grid, Stack, Loader, Alert, Tabs } from '@mantine/core';
import { IconAlertCircle, IconDownload } from '@tabler/icons-react';
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
  const [activeTab, setActiveTab] = useState<string | null>('discover');
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
      const installed = new Set(data.plugins.map((p: any) => p.id));
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
      <Container py="xl">
        <Stack align="center" gap="md">
          <Loader size="lg" />
          <p>Loading marketplace…</p>
        </Stack>
      </Container>
    );
  }

  if (error) {
    return (
      <Container py="xl">
        <Alert icon={<IconAlertCircle />} title="Error" color="red">
          {error}
        </Alert>
      </Container>
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
    <Container fluid py="xl" data-testid="marketplace-hub-page">
      <Tabs value={activeTab} onTabChange={setActiveTab} defaultValue="discover">
        <Tabs.List>
          <Tabs.Tab value="discover">Discover</Tabs.Tab>
          <Tabs.Tab value="installed">Installed</Tabs.Tab>
          <Tabs.Tab value="search">Search</Tabs.Tab>
        </Tabs.List>

        {/* ============================================================================
          TAB 1: DISCOVER
          ============================================================================ */}
        <Tabs.Panel value="discover" py="lg">
          <Stack gap="lg">
            <div>
              <h2>Featured Plugins</h2>
              <p>Discover and install plugins, skills, datasets, and templates</p>
            </div>

            {/* Card Grid — responsive */}
            <Grid data-testid="plugin-cards-grid" gutter={{ xs: 'sm', md: 'md' }}>
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
                  <Grid.Col
                    key={plugin.id}
                    span={{ xs: 12, sm: 6, md: 4, lg: 3 }}
                    data-testid={`card-${plugin.id}`}
                  >
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
                  </Grid.Col>
                );
              })}
            </Grid>
          </Stack>
        </Tabs.Panel>

        {/* ============================================================================
          TAB 2: INSTALLED
          ============================================================================ */}
        <Tabs.Panel value="installed" py="lg">
          <Stack gap="lg">
            <div>
              <h2>Installed Plugins</h2>
              <p>{installedPlugins.size} plugin(s) installed</p>
            </div>

            {installedPlugins.size > 0 ? (
              <Grid gutter={{ xs: 'sm', md: 'md' }}>
                {plugins
                  .filter((p) => installedPlugins.has(p.id))
                  .map((plugin) => (
                    <Grid.Col key={plugin.id} span={{ xs: 12, sm: 6, md: 4, lg: 3 }}>
                      <PluginCard
                        id={plugin.id}
                        name={plugin.name}
                        version={plugin.version}
                        description={plugin.description}
                        onInstall={() => console.log('Already installed:', plugin.id)}
                      />
                    </Grid.Col>
                  ))}
              </Grid>
            ) : (
              <Alert>No plugins installed yet. Explore the Discover tab to get started!</Alert>
            )}
          </Stack>
        </Tabs.Panel>

        {/* ============================================================================
          TAB 3: SEARCH
          ============================================================================ */}
        <Tabs.Panel value="search" py="lg">
          <MarketplaceSearch
            data={searchResults}
            onResultSelect={handleSearchResult}
          />
        </Tabs.Panel>
      </Tabs>
    </Container>
  );
};

export default MarketplaceHubPage;
