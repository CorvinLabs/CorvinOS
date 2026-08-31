/**
 * Marketplace Panel - CONCEPT-0023 Phase 1-2
 * Browse, search, preview, install marketplace plugins
 *
 * Phase 2: Full install/uninstall workflow with progress tracking and state management
 */

import React, { useState, useEffect } from 'react'
import { Search, Package, ExternalLink, Download, AlertCircle, Check, Loader, Github } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { InstallProgress } from '@/components/install-progress'
import { CustomRepositoriesSection } from '@/components/CustomRepositoriesSection'
import { useProgressPolling } from '@/hooks/useProgressPolling'
import { BASE } from '@/lib/api/client'

interface Plugin {
  id: string
  name: string
  version: string
  category: string
  description: string
  tier: 'buildin' | 'contributor'
  author: string
  rating?: number
  installs?: number
}

interface PluginListResponse {
  plugins: Plugin[]
  count: number
  filtered_by?: {
    category?: string
    tier?: string
  }
}

interface InstallProgress {
  extension_id: string
  status: 'pending' | 'installing' | 'success' | 'error'
  message?: string
  job_id?: string
}

export const MarketplacePanel: React.FC = () => {
  const queryClient = useQueryClient()
  const [view, setView] = useState<'browse' | 'installed' | 'custom'>('browse')
  const [plugins, setPlugins] = useState<Plugin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedPlugin, setSelectedPlugin] = useState<Plugin | null>(null)
  const [category, setCategory] = useState('')
  const [installProgress, setInstallProgress] = useState<Record<string, InstallProgress>>({})
  const [installingExtensionId, setInstallingExtensionId] = useState<string | null>(null)
  const [currentJobId, setCurrentJobId] = useState<string | null>(null)
  const isMountedRef = React.useRef(true)

  // Phase 3: Real job API polling
  const { stopPolling } = useProgressPolling(currentJobId, {
    interval: 500,
    onComplete: (status) => {
      if (installingExtensionId) {
        handleInstallComplete(installingExtensionId, status)
      }
    },
    onError: (error) => {
      console.error('Install polling error:', error)
      if (installingExtensionId) {
        setInstallProgress(prev => ({
          ...prev,
          [installingExtensionId]: {
            extension_id: installingExtensionId,
            status: 'error',
            message: error.message
          }
        }))
      }
    },
  })

  useEffect(() => {
    isMountedRef.current = true
    return () => { isMountedRef.current = false }
  }, [])

  useEffect(() => {
    fetchMarketplace()
  }, [])

  // Load installed plugins when view changes
  useEffect(() => {
    if (view === 'installed') {
      fetchInstalledPlugins()
    }
  }, [view])

  const fetchMarketplace = async () => {
    try {
      setLoading(true)
      setError(null)
      // New ADR-0511 API: /api/v1/marketplace/plugins (with optional category/tier filters)
      const url = new URL(`${BASE}/api/v1/marketplace/plugins`)
      if (category) url.searchParams.append('category', category)

      const response = await fetch(url.toString())
      if (!response.ok) throw new Error(`Failed: ${response.statusText}`)
      const data: PluginListResponse = await response.json()
      if (isMountedRef.current) {
        setPlugins(data.plugins || [])
      }
    } catch (err) {
      if (isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to fetch marketplace plugins')
      }
    } finally {
      if (isMountedRef.current) setLoading(false)
    }
  }

  const fetchInstalledPlugins = async () => {
    try {
      setLoading(true)
      setError(null)
      const response = await fetch(`${BASE}/api/v2/marketplace/installed`)
      if (!response.ok) throw new Error(`Failed: ${response.statusText}`)
      const data = await response.json()
      if (isMountedRef.current) {
        setExtensions(data.extensions || [])
      }
    } catch (err) {
      if (isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to fetch installed plugins')
      }
    } finally {
      if (isMountedRef.current) setLoading(false)
    }
  }

  const handleInstall = async (plugin: Plugin) => {
    // Phase 3 Task #7: Real job API wiring
    // 1. POST to queue install
    // 2. Get job_id
    // 3. Start polling with useProgressPolling hook
    try {
      const extensionId = plugin.id
      setInstallingExtensionId(extensionId)
      setInstallProgress(prev => ({
        ...prev,
        [extensionId]: { extension_id: extensionId, status: 'installing' }
      }))

      // Real API call: POST /api/v1/marketplace/plugins/{id}/install (future phase 4)
      // For now, this is a placeholder; Phase 4 will wire the install API
      const response = await fetch(`${BASE}/api/v1/marketplace/plugins/${extensionId}/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: plugin.version,
          tenant_id: 'default'
        })
      })

      if (!response.ok) {
        throw new Error(`Failed to queue install: ${response.statusText}`)
      }

      const data = await response.json()
      const jobId = data.job_id

      if (!jobId) {
        throw new Error('No job_id returned from install endpoint')
      }

      // Start polling with hook
      setCurrentJobId(jobId)
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Failed to start install'
      if (installingExtensionId) {
        setInstallProgress(prev => ({
          ...prev,
          [installingExtensionId]: {
            extension_id: installingExtensionId,
            status: 'error',
            message: errorMsg
          }
        }))
      }
    }
  }

  const handleInstallComplete = async (extensionId: string, _pollStatus?: any) => {
    // Called when polling completes (from useProgressPolling onComplete)
    try {
      stopPolling()
      setCurrentJobId(null)
      queryClient.invalidateQueries({ queryKey: ['plugins'] })
      setInstallProgress(prev => ({
        ...prev,
        [extensionId]: {
          extension_id: extensionId,
          status: 'success',
          message: 'Installation completed',
          job_id: currentJobId || undefined
        }
      }))
    } catch (err) {
      console.error('Error completing install:', err)
    }
  }

  const handleInstallClose = () => {
    stopPolling()
    setCurrentJobId(null)
    setInstallingExtensionId(null)
  }

  const filteredPlugins = plugins.filter(plugin => {
    const matchesSearch = searchTerm === '' ||
      plugin.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      plugin.description.toLowerCase().includes(searchTerm.toLowerCase())
    const matchesCategory = category === '' || plugin.category === category
    return matchesSearch && matchesCategory
  })

  const categories = [...new Set(plugins.map(p => p.category))]

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
      {/* Header */}
      <div className="sticky top-0 z-40 bg-white dark:bg-slate-900 border-b border-slate-200 dark:border-slate-700 shadow-sm">
        <div className="max-w-6xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between mb-4">
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
              <Package className="w-6 h-6 text-blue-600" />
              Marketplace
            </h1>
            <button
              onClick={fetchMarketplace}
              className="px-3 py-1 text-sm bg-slate-200 dark:bg-slate-800 rounded hover:bg-slate-300 dark:hover:bg-slate-700"
            >
              Refresh
            </button>
          </div>

          {/* Tabs */}
          <div className="flex gap-4 border-b border-slate-200 dark:border-slate-700">
            <button
              onClick={() => setView('browse')}
              className={`px-4 py-2 font-medium transition ${
                view === 'browse'
                  ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-600'
                  : 'text-slate-600 dark:text-slate-400'
              }`}
            >
              Browse
            </button>
            <button
              onClick={() => setView('installed')}
              className={`px-4 py-2 font-medium transition ${
                view === 'installed'
                  ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-600'
                  : 'text-slate-600 dark:text-slate-400'
              }`}
            >
              Installed
            </button>
            <button
              onClick={() => setView('custom')}
              className={`px-4 py-2 font-medium transition flex items-center gap-2 ${
                view === 'custom'
                  ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-600'
                  : 'text-slate-600 dark:text-slate-400'
              }`}
            >
              <Github className="w-4 h-4" />
              Custom Repos
            </button>
          </div>
        </div>
      </div>

      {/* Browse View */}
      {view === 'browse' && (
        <div className="max-w-6xl mx-auto px-6 py-8">
          {/* Filters */}
          <div className="mb-8 space-y-4">
            <div className="relative">
              <Search className="absolute left-3 top-3 w-5 h-5 text-slate-400" />
              <input
                type="text"
                placeholder="Search extensions..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full pl-10 pr-4 py-2 bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded-lg"
              />
            </div>
            <div className="flex gap-2 flex-wrap">
              <button
                onClick={() => setCategory('')}
                className={`px-3 py-1 rounded text-sm ${
                  category === ''
                    ? 'bg-blue-600 text-white'
                    : 'bg-slate-200 dark:bg-slate-800 text-slate-900 dark:text-slate-100'
                }`}
              >
                All
              </button>
              {categories.map(cat => (
                <button
                  key={cat}
                  onClick={() => setCategory(cat)}
                  className={`px-3 py-1 rounded text-sm ${
                    category === cat
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-200 dark:bg-slate-800 text-slate-900 dark:text-slate-100'
                  }`}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="mb-6 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 rounded-lg flex gap-3">
              <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
              <div className="text-red-700 dark:text-red-200 text-sm">{error}</div>
            </div>
          )}

          {/* Loading */}
          {loading && (
            <div className="flex justify-center py-12">
              <div className="animate-spin w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full" />
            </div>
          )}

          {/* Grid */}
          {!loading && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {filteredPlugins.map(ext => (
                <div
                  key={ext.plugin_id}
                  onClick={() => setSelectedExtension(ext)}
                  className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-700 p-4 cursor-pointer hover:shadow-md transition"
                >
                  <div className="flex justify-between items-start mb-2">
                    <div>
                      <h3 className="font-semibold text-slate-900 dark:text-white">{ext.name}</h3>
                      <p className="text-sm text-slate-600 dark:text-slate-400">v{ext.version}</p>
                    </div>
                    <span className="text-xs px-2 py-1 bg-slate-100 dark:bg-slate-800 rounded">
                      {ext.category}
                    </span>
                  </div>
                  <p className="text-sm text-slate-600 dark:text-slate-300 mb-3 line-clamp-2">
                    {ext.description}
                  </p>
                  <div className="flex justify-between items-center text-xs text-slate-500">
                    <span>{ext.download_count} downloads</span>
                    <span>★ {ext.rating_average.toFixed(1)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {!loading && filteredPlugins.length === 0 && (
            <div className="text-center py-12">
              <Package className="w-12 h-12 text-slate-300 dark:text-slate-600 mx-auto mb-3" />
              <p className="text-slate-600 dark:text-slate-400">No extensions found</p>
            </div>
          )}
        </div>
      )}

      {/* Installed View */}
      {view === 'installed' && (
        <div className="max-w-6xl mx-auto px-6 py-8">
          {loading ? (
            <div className="text-center py-8">
              <Loader className="w-8 h-8 animate-spin mx-auto" />
            </div>
          ) : extensions.length === 0 ? (
            <div className="bg-white dark:bg-slate-900 rounded-lg p-8 text-center">
              <Check className="w-12 h-12 text-green-600 mx-auto mb-3" />
              <p className="text-slate-600 dark:text-slate-400">No installed extensions yet</p>
              <button
                onClick={() => setView('browse')}
                className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                Browse Marketplace
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {extensions.map(ext => (
                <div key={ext.plugin_id} className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-700 p-4 hover:shadow-md transition-shadow">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <h3 className="font-semibold text-slate-900 dark:text-white">{ext.name || ext.plugin_id}</h3>
                      <p className="text-xs text-slate-500">{ext.version}</p>
                    </div>
                    <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-200">
                      Active
                    </span>
                  </div>
                  <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">{ext.category}</p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setSelectedExtension(ext as any)}
                      className="flex-1 px-3 py-2 text-sm bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-white rounded hover:bg-slate-200 dark:hover:bg-slate-700"
                    >
                      Details
                    </button>
                    <button className="px-3 py-2 text-sm bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-200 rounded hover:bg-red-200 dark:hover:bg-red-900">
                      Remove
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Custom Repositories View */}
      {view === 'custom' && (
        <div className="max-w-6xl mx-auto px-6 py-8">
          <CustomRepositoriesSection />
        </div>
      )}

      {/* Detail Modal */}
      {selectedExtension && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-white dark:bg-slate-900 rounded-lg max-w-2xl w-full my-8 max-h-[90vh] overflow-y-auto">
            <div className="sticky top-0 bg-slate-50 dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700 p-6 flex justify-between items-start">
              <div>
                <h2 className="text-2xl font-bold text-slate-900 dark:text-white">
                  {selectedExtension.name}
                </h2>
                <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
                  v{selectedExtension.version} • {selectedExtension.category}
                </p>
              </div>
              <button
                onClick={() => setSelectedExtension(null)}
                className="text-2xl text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
              >
                ✕
              </button>
            </div>

            <div className="p-6 space-y-6">
              <div>
                <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-2 uppercase">
                  Description
                </h3>
                <p className="text-slate-600 dark:text-slate-300">{selectedExtension.description}</p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-slate-600 dark:text-slate-400 uppercase">Author</p>
                  <p className="text-sm font-medium text-slate-900 dark:text-white">
                    {selectedExtension.author_id}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-slate-600 dark:text-slate-400 uppercase">Downloads</p>
                  <p className="text-sm font-medium text-slate-900 dark:text-white">
                    {selectedExtension.download_count}
                  </p>
                </div>
              </div>

              {/* Install status message */}
              {installProgress[selectedExtension.plugin_id] && (
                <div className={`p-3 rounded-lg text-sm ${
                  installProgress[selectedExtension.plugin_id].status === 'success'
                    ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-200'
                    : installProgress[selectedExtension.plugin_id].status === 'error'
                    ? 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-200'
                    : 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-200'
                }`}>
                  <div className="flex items-center gap-2">
                    {installProgress[selectedExtension.plugin_id].status === 'installing' && (
                      <Loader className="w-4 h-4 animate-spin" />
                    )}
                    {installProgress[selectedExtension.plugin_id].status === 'success' && (
                      <Check className="w-4 h-4" />
                    )}
                    {installProgress[selectedExtension.plugin_id].message}
                  </div>
                </div>
              )}

              <div className="flex gap-3 pt-4 border-t border-slate-200 dark:border-slate-700">
                <button
                  onClick={() => handleInstall(selectedExtension)}
                  disabled={installProgress[selectedExtension.plugin_id]?.status === 'installing'}
                  className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-slate-400 font-medium flex items-center justify-center gap-2"
                >
                  {installProgress[selectedExtension.plugin_id]?.status === 'installing' ? (
                    <>
                      <Loader className="w-4 h-4 animate-spin" />
                      Installing...
                    </>
                  ) : (
                    <>
                      <Download className="w-4 h-4" />
                      Install
                    </>
                  )}
                </button>
                <button className="flex-1 px-4 py-2 bg-slate-200 dark:bg-slate-800 text-slate-900 dark:text-white rounded-lg hover:bg-slate-300 dark:hover:bg-slate-700 font-medium flex items-center justify-center gap-2">
                  <ExternalLink className="w-4 h-4" />
                  GitHub
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Install Progress Modal (Phase 2 Week 2) */}
      {installingExtensionId && extensions.find(e => e.plugin_id === installingExtensionId) && (
        <InstallProgress
          extensionId={installingExtensionId}
          extensionName={extensions.find(e => e.plugin_id === installingExtensionId)?.name || 'Unknown'}
          onClose={handleInstallClose}
          onComplete={() => handleInstallComplete(installingExtensionId)}
        />
      )}
    </div>
  )
}

export default MarketplacePanel
