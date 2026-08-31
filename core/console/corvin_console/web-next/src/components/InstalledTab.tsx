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
import { Check, Settings, Trash2 } from 'lucide-react'
import { listPlugins } from '@/lib/api/plugins'

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
        <div className="animate-spin w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 rounded-lg">
        <p className="text-red-700 dark:text-red-200 text-sm">{error}</p>
      </div>
    )
  }

  if (installedPlugins.length === 0) {
    return (
      <div className="text-center py-12">
        <Check className="w-12 h-12 text-slate-300 dark:text-slate-600 mx-auto mb-3" />
        <p className="text-slate-600 dark:text-slate-400">No marketplace plugins installed</p>
        <p className="text-sm text-slate-500 dark:text-slate-500 mt-2">
          Go to the Marketplace tab to discover and install plugins.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
        Installed Marketplace Plugins ({installedPlugins.length})
      </h3>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {installedPlugins.map((plugin) => (
          <div
            key={plugin.plugin_id}
            className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-700 p-4"
          >
            <div className="flex justify-between items-start mb-2">
              <div>
                <h4 className="font-semibold text-slate-900 dark:text-white">{plugin.name}</h4>
                <p className="text-sm text-slate-600 dark:text-slate-400">v{plugin.version}</p>
              </div>
              {plugin.enabled ? (
                <span className="text-green-600 dark:text-green-400">
                  <Check className="w-5 h-5" />
                </span>
              ) : (
                <span className="text-slate-400">disabled</span>
              )}
            </div>

            {plugin.category && (
              <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">
                Category: {plugin.category}
              </p>
            )}

            <div className="flex gap-2">
              <button className="flex-1 px-3 py-2 text-sm bg-slate-100 dark:bg-slate-800 rounded hover:bg-slate-200 dark:hover:bg-slate-700 flex items-center justify-center gap-2">
                <Settings className="w-4 h-4" />
                Settings
              </button>
              <button className="flex-1 px-3 py-2 text-sm bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded hover:bg-red-100 dark:hover:bg-red-900/40 flex items-center justify-center gap-2">
                <Trash2 className="w-4 h-4" />
                Uninstall
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="text-xs text-slate-500 dark:text-slate-400 mt-4">
        Tip: Use Settings to configure each plugin. Live updates sync with PluginsPage.
      </div>
    </div>
  )
}

export default InstalledTab
