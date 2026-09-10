/**
 * InstalledTab — Show marketplace plugins that are currently installed
 *
 * Syncs with PluginsPage registry in real-time:
 * - When user installs from Marketplace, it appears here immediately
 * - When user uninstalls from PluginsPage, it disappears here
 * - Live-sync via query invalidation
 */

import React, { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Check, Settings, Trash2, Loader2 } from 'lucide-react'
import { listPlugins } from '@/lib/api/plugins'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

interface InstalledPlugin {
  plugin_id: string
  name: string
  version: string
  category?: string
  enabled: boolean
  installed_at?: string
}

export const InstalledTab: React.FC = () => {
  const queryClient = useQueryClient()
  const [installedPlugins, setInstalledPlugins] = useState<InstalledPlugin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Fetch installed plugins from PluginsPage registry
  const fetchInstalledPlugins = async () => {
    try {
      setLoading(true)
      setError(null)

      // Call the existing listPlugins API (from PluginsPage)
      const response = await listPlugins()

      if (response && response.plugins) {
        // Filter for marketplace-installed plugins (or all for now)
        setInstalledPlugins(response.plugins as unknown as InstalledPlugin[])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch installed plugins')
    } finally {
      setLoading(false)
    }
  }

  // Initial fetch
  useEffect(() => {
    fetchInstalledPlugins()
  }, [])

  // Subscribe to plugin list changes (live-sync)
  useEffect(() => {
    // Invalidate plugins query when marketplace install completes
    const unsubscribe = queryClient.getQueryCache().subscribe((event) => {
      if (event.type === 'updated' && event.query?.queryKey?.[0] === 'plugins') {
        // Re-fetch installed plugins
        fetchInstalledPlugins()
      }
    })

    return () => unsubscribe()
  }, [queryClient])

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-8 h-8 animate-spin text-accent" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-4 rounded-lg border border-destructive/40 bg-destructive/10">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    )
  }

  if (installedPlugins.length === 0) {
    return (
      <div className="text-center py-12">
        <Check className="w-12 h-12 text-muted-foreground/40 mx-auto mb-3" />
        <p className="text-muted-foreground">No marketplace plugins installed</p>
        <p className="text-sm text-muted-foreground/80 mt-2">
          Go to the Marketplace tab to discover and install plugins.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold text-foreground">
        Installed Marketplace Plugins ({installedPlugins.length})
      </h3>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {installedPlugins.map((plugin) => (
          <Card key={plugin.plugin_id}>
            <CardContent className="p-4">
              <div className="flex justify-between items-start mb-2">
                <div>
                  <h4 className="font-semibold text-foreground">{plugin.name}</h4>
                  <p className="text-sm text-muted-foreground">v{plugin.version}</p>
                </div>
                {plugin.enabled ? (
                  <span className="text-emerald-600 dark:text-emerald-400">
                    <Check className="w-5 h-5" />
                  </span>
                ) : (
                  <Badge variant="secondary" className="text-[10px]">disabled</Badge>
                )}
              </div>

              {plugin.category && (
                <p className="text-xs text-muted-foreground mb-3">
                  Category: {plugin.category}
                </p>
              )}

              <div className="flex gap-2">
                <Button variant="secondary" size="sm" className="flex-1">
                  <Settings className="w-4 h-4" />
                  Settings
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="flex-1 text-destructive hover:bg-destructive/10 hover:text-destructive"
                >
                  <Trash2 className="w-4 h-4" />
                  Uninstall
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="text-xs text-muted-foreground">
        Tip: Use Settings to configure each plugin. Live updates sync with PluginsPage.
      </div>
    </div>
  )
}

export default InstalledTab
