/**
 * Package Marketplace UI — ADR-0268 Phase 4
 *
 * Features:
 * - Upload ZIP packages
 * - List installed packages
 * - View package details
 * - Manage permissions
 * - Uninstall packages
 */

import React, { useState, useEffect } from 'react'

interface Package {
  id: string
  version: string
  name: string
  path: string
  installed_at: string
  enabled: boolean
}

interface UploadResponse {
  status: string
  package_id: string
  version: string
  display_name: string
  permissions: Array<{ permission: string; required: boolean; description: string }>
}

export const PackageMarketplace: React.FC = () => {
  const [packages, setPackages] = useState<Package[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploadStatus, setUploadStatus] = useState<string | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const isMountedRef = React.useRef(true)
  const pendingTimeoutsRef = React.useRef<NodeJS.Timeout[]>([])

  useEffect(() => {
    isMountedRef.current = true
    fetchPackages()
    return () => {
      isMountedRef.current = false
      // Clean up all pending timeouts on unmount
      pendingTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId))
      pendingTimeoutsRef.current = []
    }
  }, [])

  const fetchPackages = async () => {
    try {
      setLoading(true)
      const response = await fetch('/api/v1/packages')
      if (!response.ok) throw new Error(`Failed to fetch packages: ${response.statusText}`)
      const data = await response.json()
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

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      if (!file.name.endsWith('.zip')) {
        setError('Please select a ZIP file')
        return
      }
      setSelectedFile(file)
      setError(null)
    }
  }

  const handleUpload = async () => {
    if (!selectedFile) {
      setError('Please select a ZIP file')
      return
    }

    try {
      if (isMountedRef.current) setUploadStatus('Uploading...')
      const formData = new FormData()
      formData.append('file', selectedFile)

      const response = await fetch('/api/v1/packages/upload', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        let errorDetail = `Upload failed: ${response.statusText}`
        try {
          const errorData = await response.json()
          errorDetail = errorData.error || errorData.detail || errorDetail
        } catch {
          // Response wasn't JSON, use statusText
        }
        throw new Error(errorDetail)
      }

      const data: UploadResponse = await response.json()
      if (isMountedRef.current) {
        setUploadStatus(`Package ${data.display_name} uploaded successfully!`)
        setSelectedFile(null)
      }

      // Schedule package list refresh with proper cleanup
      const timeoutId = setTimeout(() => {
        // Remove from pending list when it fires (callback executed)
        const index = pendingTimeoutsRef.current.indexOf(timeoutId)
        if (index >= 0) {
          pendingTimeoutsRef.current.splice(index, 1)
        }
        if (isMountedRef.current) {
          fetchPackages()
          setUploadStatus(null)
        }
      }, 1500)

      // Add to pending BEFORE scheduling (so cleanup can clear it if needed)
      pendingTimeoutsRef.current.push(timeoutId)
    } catch (err) {
      if (isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Upload failed')
        setUploadStatus(null)
      }
    }
  }

  const handleUninstall = async (packageId: string) => {
    if (!confirm('Are you sure you want to uninstall this package?')) {
      return
    }

    try {
      if (isMountedRef.current) setUploadStatus(`Uninstalling ${packageId}...`)
      const response = await fetch(`/api/v1/packages/${packageId}`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        let errorDetail = `Uninstall failed: ${response.statusText}`
        try {
          const errorData = await response.json()
          errorDetail = errorData.error || errorData.detail || errorDetail
        } catch {
          // Response wasn't JSON, use statusText
        }
        throw new Error(errorDetail)
      }

      if (isMountedRef.current) setUploadStatus('Package uninstalled successfully!')

      const timeoutId = setTimeout(() => {
        // Remove from pending list when it fires
        const index = pendingTimeoutsRef.current.indexOf(timeoutId)
        if (index >= 0) {
          pendingTimeoutsRef.current.splice(index, 1)
        }
        if (isMountedRef.current) {
          fetchPackages()
          setUploadStatus(null)
        }
      }, 1500)

      pendingTimeoutsRef.current.push(timeoutId)
    } catch (err) {
      if (isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Uninstall failed')
        setUploadStatus(null)
      }
    }
  }

  return (
    <div className="package-marketplace">
      <h1>Package Marketplace</h1>

      {/* Upload Section */}
      <section className="upload-section">
        <h2>Upload Package</h2>
        <div className="upload-widget">
          <input
            type="file"
            accept=".zip"
            onChange={handleFileSelect}
            className="file-input"
            id="package-file"
          />
          <label htmlFor="package-file" className="file-label">
            {selectedFile ? selectedFile.name : 'Select ZIP package...'}
          </label>
          <button
            onClick={handleUpload}
            disabled={!selectedFile || uploadStatus !== null}
            className="upload-button"
          >
            {uploadStatus ? uploadStatus : 'Upload Package'}
          </button>
        </div>
      </section>

      {/* Messages */}
      {error && <div className="error-message">{error}</div>}
      {uploadStatus && <div className="status-message">{uploadStatus}</div>}

      {/* Installed Packages */}
      <section className="packages-section">
        <h2>Installed Packages ({packages.length})</h2>
        {loading ? (
          <p>Loading packages...</p>
        ) : packages.length === 0 ? (
          <p className="empty-message">No packages installed yet</p>
        ) : (
          <div className="packages-list">
            {packages.map((pkg) => (
              <div key={pkg.id} className="package-card">
                <div className="package-header">
                  <h3>{pkg.name}</h3>
                  <span className="version">{pkg.version}</span>
                </div>
                <div className="package-meta">
                  <p className="id">ID: {pkg.id}</p>
                  <p className="status">
                    Status: <span className={pkg.enabled ? 'enabled' : 'disabled'}>
                      {pkg.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </p>
                  <p className="installed">
                    Installed: {new Date(pkg.installed_at).toLocaleDateString()}
                  </p>
                </div>
                <div className="package-actions">
                  <button
                    onClick={() => handleUninstall(pkg.id)}
                    className="uninstall-button"
                  >
                    Uninstall
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <style>{`
        .package-marketplace {
          max-width: 1000px;
          margin: 0 auto;
          padding: 20px;
        }

        .upload-section {
          background: #f5f5f5;
          padding: 20px;
          border-radius: 8px;
          margin-bottom: 30px;
        }

        .upload-widget {
          display: flex;
          gap: 10px;
          align-items: center;
        }

        .file-input {
          display: none;
        }

        .file-label {
          flex: 1;
          padding: 10px;
          border: 2px dashed #ccc;
          border-radius: 4px;
          cursor: pointer;
          background: white;
        }

        .upload-button {
          padding: 10px 20px;
          background: #0066cc;
          color: white;
          border: none;
          border-radius: 4px;
          cursor: pointer;
          font-weight: bold;
        }

        .upload-button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .error-message {
          background: #ffebee;
          color: #d32f2f;
          border-left: 4px solid #d32f2f;
          padding: 12px;
          border-radius: 4px;
          margin-bottom: 20px;
        }

        .status-message {
          background: #e8f5e9;
          color: #2e7d32;
          border-left: 4px solid #2e7d32;
          padding: 12px;
          border-radius: 4px;
          margin-bottom: 20px;
        }

        .packages-section {
          margin-top: 40px;
        }

        .packages-list {
          display: grid;
          gap: 15px;
        }

        .package-card {
          border: 1px solid #ddd;
          border-radius: 8px;
          padding: 15px;
          background: white;
        }

        .package-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 10px;
        }

        .package-header h3 {
          margin: 0;
        }

        .version {
          background: #eee;
          padding: 4px 8px;
          border-radius: 4px;
          font-size: 0.9em;
          font-weight: bold;
        }

        .package-meta p {
          margin: 5px 0;
          font-size: 0.9em;
        }

        .status {
          margin: 8px 0;
        }

        .enabled {
          color: green;
          font-weight: bold;
        }

        .disabled {
          color: orange;
          font-weight: bold;
        }

        .package-actions {
          display: flex;
          gap: 10px;
          margin-top: 10px;
        }

        .uninstall-button {
          padding: 6px 12px;
          background: #dc3545;
          color: white;
          border: none;
          border-radius: 4px;
          cursor: pointer;
          font-size: 0.9em;
        }

        .uninstall-button:hover {
          background: #c82333;
        }

        .empty-message {
          color: #666;
          text-align: center;
          padding: 40px 20px;
        }
      `}</style>
    </div>
  )
}

export default PackageMarketplace
