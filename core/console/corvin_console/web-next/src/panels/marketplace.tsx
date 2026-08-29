/**
 * Marketplace Panel - CONCEPT-0023 Phase 1-2
 * Browse, search, preview, install marketplace plugins
 *
 * Phase 2: Full install/uninstall workflow with progress tracking and state management
 */

import React, { useState, useEffect } from 'react'
import { Search, Package, ExternalLink, Download, AlertCircle, Check, Loader } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useProgressPolling } from '@/hooks/useProgressPolling'
import { ToastNotification, ToastMessage } from '@/components/ToastNotification'

interface Extension {
  plugin_id: string
  name: string
  version: string
  category: string
  description: string
  author_id: string
  rating_average: number
  download_count: number
  cached?: boolean
}

interface IndexResponse {
  version: string
  extensions: Extension[]
  cached?: boolean
}

interface InstallProgress {
  extension_id: string
  status: 'pending' | 'installing' | 'success' | 'error'
  message?: string
  job_id?: string
}

export const MarketplacePanel: React.FC = () => {
  const queryClient = useQueryClient()
  const [view, setView] = useState<'browse' | 'installed'>('browse')
  const [extensions, setExtensions] = useState<Extension[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedExtension, setSelectedExtension] = useState<Extension | null>(null)
  const [category, setCategory] = useState('')
  const [installProgress, setInstallProgress] = useState<Record<string, InstallProgress>>({})
  const isMountedRef = React.useRef(true)

  useEffect(() => {
    isMountedRef.current = true
    return () => { isMountedRef.current = false }
  }, [])

  useEffect(() => {
    fetchMarketplace()
  }, [])

  const fetchMarketplace = async () => {
    try {
      setLoading(true)
      setError(null)
      const response = await fetch('/api/v2/marketplace/index')
      if (!response.ok) throw new Error(`Failed: ${response.statusText}`)
      const data: IndexResponse = await response.json()
      if (isMountedRef.current) {
        setExtensions(data.extensions || [])
      }
    } catch (err) {
      if (isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to fetch marketplace')
      }
    } finally {
      if (isMountedRef.current) setLoading(false)
    }
  }

  const handleInstall = async (extension: Extension) => {
    const extensionId = extension.plugin_id
    setInstallProgress(prev => ({
      ...prev,
      [extensionId]: { extension_id: extensionId, status: 'installing' }
    }))

    try {
      const response = await fetch('/api/v2/marketplace/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          extension_id: extensionId,
          version: extension.version,
          tenant_id: 'default'
        })
      })

      if (!response.ok) {
        throw new Error(`Failed to install: ${response.statusText}`)
      }

      const data = await response.json()
      if (isMountedRef.current) {
        setInstallProgress(prev => ({
          ...prev,
          [extensionId]: {
            extension_id: extensionId,
            status: 'success',
            message: 'Installation queued',
            job_id: data.job_id
          }
        }))
        // Invalidate plugins query to refresh the plugins list
        queryClient.invalidateQueries({ queryKey: ['plugins'] })
        // Auto-close modal after 2 seconds
        setTimeout(() => {
          setSelectedExtension(null)
        }, 2000)
      }
    } catch (err) {
      if (isMountedRef.current) {
        setInstallProgress(prev => ({
          ...prev,
          [extensionId]: {
            extension_id: extensionId,
            status: 'error',
            message: err instanceof Error ? err.message : 'Installation failed'
          }
        }))
      }
    }
  }

  const filteredExtensions = extensions.filter(ext => {
    const matchesSearch = searchTerm === '' ||
      ext.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      ext.description.toLowerCase().includes(searchTerm.toLowerCase())
    const matchesCategory = category === '' || ext.category === category
    return matchesSearch && matchesCategory
  })

  const categories = [...new Set(extensions.map(e => e.category))]

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
              {filteredExtensions.map(ext => (
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

          {!loading && filteredExtensions.length === 0 && (
            <div className="text-center py-12">
              <Package className="w-12 h-12 text-slate-300 dark:text-slate-600 mx-auto mb-3" />
              <p className="text-slate-600 dark:text-slate-400">No extensions found</p>
            </div>
          )}
        </div>
      )}

      {/* Installed View (Placeholder) */}
      {view === 'installed' && (
        <div className="max-w-6xl mx-auto px-6 py-8">
          <div className="bg-white dark:bg-slate-900 rounded-lg p-8 text-center">
            <Check className="w-12 h-12 text-green-600 mx-auto mb-3" />
            <p className="text-slate-600 dark:text-slate-400">Installed extensions will appear here (Phase 4)</p>
          </div>
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
    </div>
  )
}

export default MarketplacePanel
