/**
 * Package Marketplace — ADR-0268 Phase 4
 *
 * Beautiful, user-friendly marketplace for Skills & Extensions
 * with package details, README display, and easy management.
 */

import React, { useState, useEffect } from 'react'
import { X, Upload, Search, Package as PackageIcon, ExternalLink, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface PackageInfo {
  package_id: string
  version: string
  display_name: string
  description: string
  author: string
  installed_at: string
  tenant_id: string
}

interface PackageDetails {
  package_id: string
  version: string
  display_name: string
  description: string
  author: string
  license: string
  installed_at: string
  manifest: Record<string, unknown>
  dependencies: string[]
  permissions: Array<{ permission: string; required: boolean; description: string }>
  tenant_id: string
}

interface ListResponse {
  packages: PackageInfo[]
  total: number
}

export const PackageMarketplace: React.FC = () => {
  const [packages, setPackages] = useState<PackageInfo[]>([])
  const [selectedPackage, setSelectedPackage] = useState<PackageDetails | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [uploadStatus, setUploadStatus] = useState<string | null>(null)
  const [showUploadModal, setShowUploadModal] = useState(false)
  const [csrfToken, setCsrfToken] = useState<string>('')
  const isMountedRef = React.useRef(true)

  // Get CSRF token on mount
  React.useEffect(() => {
    const getCsrfToken = async () => {
      try {
        const response = await fetch('/v1/console/auth/whoami')
        if (response.ok) {
          const data = await response.json()
          setCsrfToken(data.csrf_token)
        }
      } catch (err) {
        console.error('Failed to get CSRF token:', err)
      }
    }
    getCsrfToken()
  }, [])

  useEffect(() => {
    isMountedRef.current = true
    fetchPackages()
    return () => {
      isMountedRef.current = false
    }
  }, [])

  const fetchPackages = async () => {
    try {
      setLoading(true)
      const response = await fetch('/v1/console/packages')
      if (!response.ok) throw new Error(`Failed to fetch packages: ${response.statusText}`)
      const data: ListResponse = await response.json()
      if (isMountedRef.current) {
        setPackages(data.packages || [])
        setError(null)
      }
    } catch (err) {
      if (isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to load packages')
      }
    } finally {
      if (isMountedRef.current) {
        setLoading(false)
      }
    }
  }

  const fetchPackageDetails = async (packageId: string) => {
    try {
      const response = await fetch(`/v1/console/packages/${packageId}/details`)
      if (!response.ok) throw new Error('Failed to fetch details')
      const data: PackageDetails = await response.json()
      if (isMountedRef.current) {
        setSelectedPackage(data)
      }
    } catch (err) {
      if (isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to load package details')
      }
    }
  }

  const handleUninstall = async (packageId: string) => {
    if (!confirm('Permanently uninstall this package?')) return

    if (!csrfToken) {
      if (isMountedRef.current) {
        setError('CSRF token not available')
      }
      return
    }

    try {
      const response = await fetch(`/v1/console/packages/${packageId}`, {
        method: 'DELETE',
        headers: {
          'X-CSRF-Token': csrfToken,
        },
      })
      if (!response.ok) throw new Error('Uninstall failed')
      if (isMountedRef.current) {
        setSelectedPackage(null)
        fetchPackages()
      }
    } catch (err) {
      if (isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Uninstall failed')
      }
    }
  }

  const filteredPackages = packages.filter(pkg =>
    pkg.display_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    pkg.description.toLowerCase().includes(searchTerm.toLowerCase())
  )

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="sticky top-0 z-40 bg-background/80 backdrop-blur border-b border-border">
        <div className="max-w-7xl mx-auto px-6 py-6">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold text-foreground flex items-center gap-3">
                <PackageIcon className="w-8 h-8 text-accent" />
                Package Marketplace
              </h1>
              <p className="text-muted-foreground mt-1">
                {packages.length} package{packages.length !== 1 ? 's' : ''} installed
              </p>
            </div>
            <Button variant="accent" onClick={() => setShowUploadModal(true)}>
              <Upload className="w-4 h-4" />
              Upload Package
            </Button>
          </div>

          {/* Search */}
          <div className="mt-6 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              type="text"
              placeholder="Search packages..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-7xl mx-auto px-6 py-8">
        {error && (
          <div className="mb-6 p-4 rounded-lg border border-destructive/40 bg-destructive/10 text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="animate-spin w-8 h-8 border-4 border-accent border-t-transparent rounded-full"></div>
          </div>
        ) : filteredPackages.length === 0 ? (
          <div className="text-center py-16">
            <PackageIcon className="w-16 h-16 text-muted-foreground/40 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-foreground mb-2">
              {searchTerm ? 'No packages found' : 'No packages installed yet'}
            </h3>
            <p className="text-muted-foreground">
              {searchTerm
                ? 'Try a different search term'
                : 'Upload your first package to get started'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredPackages.map((pkg) => (
              <div
                key={pkg.package_id}
                onClick={() => fetchPackageDetails(pkg.package_id)}
                className="group cursor-pointer bg-card rounded-xl shadow-sm hover:shadow-md transition-shadow border border-border overflow-hidden"
              >
                {/* Card Header */}
                <div className="h-32 bg-accent/10 border-b border-border p-4 flex flex-col justify-end">
                  <h3 className="text-xl font-bold text-foreground group-hover:text-accent transition-colors">
                    {pkg.display_name}
                  </h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    v{pkg.version}
                  </p>
                </div>

                {/* Card Body */}
                <div className="p-4">
                  <p className="text-sm text-muted-foreground line-clamp-2 mb-3">
                    {pkg.description || 'No description available'}
                  </p>

                  {pkg.author && (
                    <div className="text-xs text-muted-foreground mb-4">
                      <span className="font-semibold">By</span> {pkg.author}
                    </div>
                  )}

                  {/* Metadata */}
                  <div className="flex items-center gap-2 mb-4">
                    <Badge variant="outline" className="text-xs">
                      Installed {new Date(pkg.installed_at).toLocaleDateString()}
                    </Badge>
                  </div>

                  {/* CTA */}
                  <Button
                    variant="secondary"
                    size="sm"
                    className="w-full"
                    onClick={(e) => {
                      e.stopPropagation()
                      fetchPackageDetails(pkg.package_id)
                    }}
                  >
                    <ExternalLink className="w-4 h-4" />
                    View Details
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Details Modal */}
      {selectedPackage && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-card rounded-xl max-w-2xl w-full my-8 max-h-[90vh] overflow-y-auto border border-border">
            {/* Modal Header */}
            <div className="sticky top-0 bg-accent/10 border-b border-border p-6 flex items-start justify-between">
              <div>
                <h2 className="text-2xl font-bold text-foreground">
                  {selectedPackage.display_name}
                </h2>
                <p className="text-sm text-muted-foreground mt-1">
                  Version {selectedPackage.version} • By {selectedPackage.author || 'Unknown'}
                </p>
              </div>
              <button
                onClick={() => setSelectedPackage(null)}
                className="p-2 hover:bg-muted rounded-lg transition-colors"
              >
                <X className="w-5 h-5 text-muted-foreground" />
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-6 space-y-6">
              {/* Description */}
              {selectedPackage.description && (
                <div>
                  <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-2">
                    Description
                  </h3>
                  <p className="text-foreground/90">
                    {selectedPackage.description}
                  </p>
                </div>
              )}

              {/* License */}
              {selectedPackage.license && (
                <div>
                  <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-2">
                    License
                  </h3>
                  <code className="px-3 py-1 bg-muted text-foreground/90 rounded text-sm">
                    {selectedPackage.license}
                  </code>
                </div>
              )}

              {/* Dependencies */}
              {selectedPackage.dependencies && selectedPackage.dependencies.length > 0 && (
                <div>
                  <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-2">
                    Dependencies
                  </h3>
                  <div className="flex flex-wrap gap-2">
                    {selectedPackage.dependencies.map((dep, i) => (
                      <code
                        key={i}
                        className="px-3 py-1 bg-muted text-foreground/90 rounded text-sm"
                      >
                        {dep}
                      </code>
                    ))}
                  </div>
                </div>
              )}

              {/* Permissions */}
              {selectedPackage.permissions && selectedPackage.permissions.length > 0 && (
                <div>
                  <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">
                    Permissions Required
                  </h3>
                  <div className="space-y-2">
                    {selectedPackage.permissions.map((perm, i) => (
                      <div
                        key={i}
                        className="p-3 bg-muted/40 rounded-lg border border-border"
                      >
                        <div className="flex items-start justify-between">
                          <div>
                            <code className="text-sm font-mono text-foreground/90">
                              {perm.permission}
                            </code>
                            {perm.description && (
                              <p className="text-xs text-muted-foreground mt-1">
                                {perm.description}
                              </p>
                            )}
                          </div>
                          {perm.required && (
                            <Badge variant="warn" className="text-xs">
                              Required
                            </Badge>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Metadata */}
              <div className="grid grid-cols-2 gap-4 pt-4 border-t border-border">
                <div>
                  <p className="text-xs text-muted-foreground uppercase tracking-wide">
                    Installed
                  </p>
                  <p className="text-sm font-medium text-foreground mt-1">
                    {new Date(selectedPackage.installed_at).toLocaleDateString()}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground uppercase tracking-wide">
                    Package ID
                  </p>
                  <code className="text-sm font-mono text-foreground mt-1 break-all">
                    {selectedPackage.package_id}
                  </code>
                </div>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="sticky bottom-0 border-t border-border bg-muted/40 p-6 flex gap-3">
              <Button
                variant="outline"
                className="flex-1"
                onClick={() => setSelectedPackage(null)}
              >
                Close
              </Button>
              <Button
                variant="destructive"
                className="flex-1"
                onClick={() => {
                  handleUninstall(selectedPackage.package_id)
                }}
              >
                <Trash2 className="w-4 h-4" />
                Uninstall
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Upload Modal */}
      {showUploadModal && (
        <UploadModal
          onClose={() => setShowUploadModal(false)}
          onUpload={fetchPackages}
          onStatus={setUploadStatus}
        />
      )}

      {uploadStatus && (
        <div className="fixed bottom-6 right-6 bg-emerald-600 text-white px-4 py-3 rounded-lg shadow-lg">
          {uploadStatus}
        </div>
      )}
    </div>
  )
}

interface UploadModalProps {
  onClose: () => void
  onUpload: () => void
  onStatus: (status: string) => void
}

const UploadModal: React.FC<UploadModalProps> = ({ onClose, onUpload, onStatus }) => {
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [csrfToken, setCsrfToken] = useState<string>('')

  // Get CSRF token on mount
  React.useEffect(() => {
    const getCsrfToken = async () => {
      try {
        const response = await fetch('/v1/console/auth/whoami')
        if (response.ok) {
          const data = await response.json()
          setCsrfToken(data.csrf_token)
        }
      } catch (err) {
        console.error('Failed to get CSRF token:', err)
      }
    }
    getCsrfToken()
  }, [])

  const handleUpload = async () => {
    if (!file) {
      setError('Please select a ZIP file')
      return
    }

    if (!csrfToken) {
      setError('CSRF token not available')
      return
    }

    try {
      setUploading(true)
      setError(null)
      onStatus('Uploading package...')

      const formData = new FormData()
      formData.append('file', file)

      const response = await fetch('/v1/console/packages/upload', {
        method: 'POST',
        headers: {
          'X-CSRF-Token': csrfToken,
        },
        body: formData,
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Upload failed')
      }

      onStatus('Package uploaded successfully!')
      onUpload()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
      <div className="bg-card rounded-xl max-w-md w-full border border-border">
        <div className="flex items-center justify-between p-6 border-b border-border">
          <h2 className="text-xl font-bold text-foreground">
            Upload Package
          </h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-muted rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-muted-foreground" />
          </button>
        </div>

        <div className="p-6 space-y-4">
          <div className="border-2 border-dashed border-border rounded-lg p-8 text-center cursor-pointer hover:bg-muted/40 transition-colors">
            <input
              type="file"
              accept=".zip"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) {
                  setFile(f)
                  setError(null)
                }
              }}
              className="hidden"
              id="file-input"
            />
            <label htmlFor="file-input" className="cursor-pointer">
              <Upload className="w-8 h-8 text-muted-foreground mx-auto mb-2" />
              <p className="font-medium text-foreground">
                {file ? file.name : 'Click to select ZIP file'}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                or drag and drop
              </p>
            </label>
          </div>

          {error && (
            <div className="p-3 rounded-lg border border-destructive/40 bg-destructive/10 text-destructive text-sm">
              {error}
            </div>
          )}

          <div className="flex gap-3">
            <Button variant="outline" className="flex-1" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="accent"
              className={cn("flex-1", (!file || uploading) && "opacity-50 cursor-not-allowed")}
              onClick={handleUpload}
              disabled={!file || uploading}
            >
              {uploading ? 'Uploading...' : 'Upload'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default PackageMarketplace
