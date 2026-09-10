/**
 * Marketplace Panel - CONCEPT-0023 Phase 1-2
 * Browse, search, preview, install marketplace plugins
 *
 * Phase 2: Full install/uninstall workflow with progress tracking and state management
 */

import React, { useState, useEffect, useCallback } from 'react'
import { Search, Package, ExternalLink, Download, AlertCircle, Check, Loader2, Github } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { InstallProgress } from '@/components/install-progress'
import { CustomRepositoriesSection } from '@/components/CustomRepositoriesSection'
import { useProgressPolling } from '@/hooks/useProgressPolling'
import { BASE } from '@/lib/api/client'
import { useAuth } from '@/lib/auth'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface Plugin {
  id: string
  name: string
  version: string
  category: string
  description: string
  tier: 'buildin' | 'contributor'
  author: string
  rating?: number
  install_count?: number
  tags?: string[]
}

/** One record from the plugin registry surface (GET /api/v1/plugins). */
interface InstalledPlugin {
  plugin_id: string
  version: string
  display_name: string
  plugin_type: string
  origin: string
  enabled: boolean
  runtime_loaded?: boolean
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
  // Install is a mutation → CSRF token (backend: require_csrf, tenant from session).
  const { session } = useAuth()
  const csrf = session?.csrf_token ?? ''
  const queryClient = useQueryClient()
  const [view, setView] = useState<'browse' | 'installed' | 'custom'>('browse')
  const [plugins, setPlugins] = useState<Plugin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedPlugin, setSelectedPlugin] = useState<Plugin | null>(null)
  const [installed, setInstalled] = useState<InstalledPlugin[]>([])
  const [installedNotice, setInstalledNotice] = useState<string | null>(null)
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


  // Load installed plugins when view changes
  useEffect(() => {
    if (view === 'installed') {
      fetchInstalledPlugins()
    }
  }, [view])

  const fetchMarketplace = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      // New ADR-0511 API: /api/v1/marketplace/plugins (with optional category/tier filters).
      // BASE is a root-relative path, so URL() cannot parse it without an origin —
      // build the query string directly instead.
      const params = new URLSearchParams()
      if (category) params.set('category', category)
      const query = params.toString()

      const response = await fetch(
        `${BASE}/api/v1/marketplace/plugins${query ? `?${query}` : ''}`
      )
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
  }, [category])

  // Initial load + refetch whenever the category filter changes
  useEffect(() => {
    fetchMarketplace()
  }, [fetchMarketplace])

  const fetchInstalledPlugins = async () => {
    try {
      setLoading(true)
      setError(null)
      setInstalledNotice(null)
      const response = await fetch(`${BASE}/api/v1/plugins`)
      if (response.status === 404) {
        // ADR-0233: the plugin console surface ships dark; the route is mounted
        // but 404s while the flag is off. Not an error the operator can act on.
        if (isMountedRef.current) {
          setInstalled([])
          setInstalledNotice('Plugin console surface is disabled (plugin_console_surface).')
        }
        return
      }
      if (!response.ok) throw new Error(`Failed: ${response.statusText}`)
      const data = await response.json()
      if (isMountedRef.current) {
        setInstalled(data.plugins || [])
      }
    } catch (err) {
      if (isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to fetch installed plugins')
      }
    } finally {
      if (isMountedRef.current) setLoading(false)
    }
  }

  /** Render an installed record in the marketplace detail modal. */
  const toPluginView = (rec: InstalledPlugin): Plugin =>
    plugins.find(p => p.id === rec.plugin_id) ?? {
      id: rec.plugin_id,
      name: rec.display_name || rec.plugin_id,
      version: rec.version,
      category: rec.plugin_type,
      description: 'Installed plugin — no marketplace listing available.',
      tier: rec.origin === 'builtin' ? 'buildin' : 'contributor',
      author: rec.origin,
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
      const response = await fetch(`${BASE}/api/v1/marketplace/plugins/${encodeURIComponent(extensionId)}/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        // tenant is the authenticated session's — the backend ignores a body tenant_id
        body: JSON.stringify({ version: plugin.version })
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

  const handleInstallComplete = async (extensionId: string, _pollStatus?: unknown) => {
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
    <div className="bg-background">
      {/* Header */}
      <div className="sticky top-0 z-40 bg-card border-b border-border shadow-sm">
        <div className="max-w-6xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between mb-4">
            <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
              <Package className="w-6 h-6 text-accent" />
              Marketplace
            </h1>
            <Button variant="secondary" size="sm" onClick={fetchMarketplace}>
              Refresh
            </Button>
          </div>

          {/* Tabs */}
          <div className="flex gap-4 border-b border-border">
            <button
              onClick={() => setView('browse')}
              className={cn(
                'px-4 py-2 font-medium transition',
                view === 'browse'
                  ? 'text-accent border-b-2 border-accent'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              Browse
            </button>
            <button
              onClick={() => setView('installed')}
              className={cn(
                'px-4 py-2 font-medium transition',
                view === 'installed'
                  ? 'text-accent border-b-2 border-accent'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              Installed
            </button>
            <button
              onClick={() => setView('custom')}
              className={cn(
                'px-4 py-2 font-medium transition flex items-center gap-2',
                view === 'custom'
                  ? 'text-accent border-b-2 border-accent'
                  : 'text-muted-foreground hover:text-foreground',
              )}
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
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                type="text"
                placeholder="Search extensions..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="pl-10"
              />
            </div>
            <div className="flex gap-2 flex-wrap">
              <button
                onClick={() => setCategory('')}
                className={cn(
                  'px-3 py-1 rounded text-sm transition-colors',
                  category === ''
                    ? 'bg-accent text-accent-foreground'
                    : 'bg-muted text-foreground hover:bg-muted/70',
                )}
              >
                All
              </button>
              {categories.map(cat => (
                <button
                  key={cat}
                  onClick={() => setCategory(cat)}
                  className={cn(
                    'px-3 py-1 rounded text-sm transition-colors',
                    category === cat
                      ? 'bg-accent text-accent-foreground'
                      : 'bg-muted text-foreground hover:bg-muted/70',
                  )}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="mb-6 p-4 bg-destructive/10 border border-destructive/40 rounded-lg flex gap-3">
              <AlertCircle className="w-5 h-5 text-destructive flex-shrink-0 mt-0.5" />
              <div className="text-destructive text-sm">{error}</div>
            </div>
          )}

          {/* Loading */}
          {loading && (
            <div className="flex justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-accent" />
            </div>
          )}

          {/* Grid */}
          {!loading && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {filteredPlugins.map(ext => (
                <Card
                  key={ext.id}
                  onClick={() => setSelectedPlugin(ext)}
                  className="p-4 cursor-pointer hover:shadow-md"
                >
                  <div className="flex justify-between items-start mb-2">
                    <div>
                      <h3 className="font-semibold text-foreground">{ext.name}</h3>
                      <p className="text-sm text-muted-foreground">v{ext.version}</p>
                    </div>
                    <Badge variant="outline" className="text-xs">{ext.category}</Badge>
                  </div>
                  <p className="text-sm text-muted-foreground mb-3 line-clamp-2">
                    {ext.description}
                  </p>
                  <div className="flex justify-between items-center text-xs text-muted-foreground">
                    <span>{ext.install_count ?? 0} downloads</span>
                    <span>★ {(ext.rating ?? 0).toFixed(1)}</span>
                  </div>
                </Card>
              ))}
            </div>
          )}

          {!loading && filteredPlugins.length === 0 && (
            <div className="text-center py-12">
              <Package className="w-12 h-12 text-muted-foreground/50 mx-auto mb-3" />
              <p className="text-muted-foreground">No extensions found</p>
            </div>
          )}
        </div>
      )}

      {/* Installed View */}
      {view === 'installed' && (
        <div className="max-w-6xl mx-auto px-6 py-8">
          {loading ? (
            <div className="text-center py-8">
              <Loader2 className="w-8 h-8 animate-spin mx-auto text-accent" />
            </div>
          ) : installed.length === 0 ? (
            <Card className="p-8 text-center">
              <Check className="w-12 h-12 text-emerald-600 dark:text-emerald-400 mx-auto mb-3" />
              <p className="text-muted-foreground">
                {installedNotice ?? 'No installed extensions yet'}
              </p>
              <Button variant="accent" className="mt-4" onClick={() => setView('browse')}>
                Browse Marketplace
              </Button>
            </Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {installed.map(ext => (
                <Card key={ext.plugin_id} className="p-4 hover:shadow-md">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <h3 className="font-semibold text-foreground">{ext.display_name || ext.plugin_id}</h3>
                      <p className="text-xs text-muted-foreground">{ext.version}</p>
                    </div>
                    <Badge variant="ok" className="text-xs">Active</Badge>
                  </div>
                  <p className="text-sm text-muted-foreground mb-4">{ext.plugin_type}</p>
                  <div className="flex gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      className="flex-1"
                      onClick={() => setSelectedPlugin(toPluginView(ext))}
                    >
                      Details
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-destructive border-destructive/40 hover:bg-destructive/10"
                    >
                      Remove
                    </Button>
                  </div>
                </Card>
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
      {selectedPlugin && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-card border border-border rounded-lg max-w-2xl w-full my-8 max-h-[90vh] overflow-y-auto">
            <div className="sticky top-0 bg-muted/60 border-b border-border p-6 flex justify-between items-start">
              <div>
                <h2 className="text-2xl font-bold text-foreground">
                  {selectedPlugin.name}
                </h2>
                <p className="text-sm text-muted-foreground mt-1">
                  v{selectedPlugin.version} • {selectedPlugin.category}
                </p>
              </div>
              <button
                onClick={() => setSelectedPlugin(null)}
                className="text-2xl text-muted-foreground hover:text-foreground"
              >
                ✕
              </button>
            </div>

            <div className="p-6 space-y-6">
              <div>
                <h3 className="text-sm font-semibold text-muted-foreground mb-2 uppercase">
                  Description
                </h3>
                <p className="text-foreground">{selectedPlugin.description}</p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground uppercase">Author</p>
                  <p className="text-sm font-medium text-foreground">
                    {selectedPlugin.author}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground uppercase">Downloads</p>
                  <p className="text-sm font-medium text-foreground">
                    {selectedPlugin.install_count ?? 0}
                  </p>
                </div>
              </div>

              {/* Install status message */}
              {installProgress[selectedPlugin.id] && (
                <div className={cn(
                  'p-3 rounded-lg text-sm border',
                  installProgress[selectedPlugin.id].status === 'success'
                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700 dark:text-emerald-400'
                    : installProgress[selectedPlugin.id].status === 'error'
                    ? 'bg-destructive/10 border-destructive/40 text-destructive'
                    : 'bg-accent/10 border-accent/30 text-accent',
                )}>
                  <div className="flex items-center gap-2">
                    {installProgress[selectedPlugin.id].status === 'installing' && (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    )}
                    {installProgress[selectedPlugin.id].status === 'success' && (
                      <Check className="w-4 h-4" />
                    )}
                    {installProgress[selectedPlugin.id].message}
                  </div>
                </div>
              )}

              <div className="flex gap-3 pt-4 border-t border-border">
                <Button
                  variant="accent"
                  className="flex-1"
                  onClick={() => handleInstall(selectedPlugin)}
                  disabled={installProgress[selectedPlugin.id]?.status === 'installing'}
                >
                  {installProgress[selectedPlugin.id]?.status === 'installing' ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Installing...
                    </>
                  ) : (
                    <>
                      <Download className="w-4 h-4" />
                      Install
                    </>
                  )}
                </Button>
                <Button variant="secondary" className="flex-1">
                  <ExternalLink className="w-4 h-4" />
                  GitHub
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Install Progress Modal (Phase 2 Week 2) */}
      {installingExtensionId && plugins.find(p => p.id === installingExtensionId) && (
        <InstallProgress
          extensionId={installingExtensionId}
          extensionName={plugins.find(p => p.id === installingExtensionId)?.name || 'Unknown'}
          onClose={handleInstallClose}
          onComplete={() => handleInstallComplete(installingExtensionId)}
        />
      )}
    </div>
  )
}

export default MarketplacePanel
