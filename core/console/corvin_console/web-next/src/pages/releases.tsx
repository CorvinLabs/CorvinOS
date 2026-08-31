/**
 * Release Manager Panel — Versioned Skill Releases
 *
 * Features:
 * - Release history with semantic versioning
 * - Changelog management
 * - Release notes generation
 * - Rollback eligibility checking
 * - Breaking change tracking
 */

import { useState, useEffect } from 'react'
import { Package, Plus } from 'lucide-react'

interface Release {
  skill_id: string
  version: string
  timestamp: string
  description: string
  author: string
  changes: string[]
  skills_included: number
  breaking_changes: boolean
  tags: string[]
}

export default function ReleaseManagerPanel({ skillId }: { skillId?: string } = {}) {
  const [releases, setReleases] = useState<Release[]>([])
  const [latestVersion, setLatestVersion] = useState<string | null>(null)
  const [nextVersion, setNextVersion] = useState<string | null>(null)
  const [bumpType] = useState<'major' | 'minor' | 'patch'>('patch')
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [changelog, setChangelog] = useState<string | null>(null)

  // Load releases on mount
  useEffect(() => {
    fetchReleases()
  }, [skillId])

  const fetchReleases = async () => {
    try {
      const response = await fetch(`/api/console/releases/${skillId}`)
      const data = await response.json()
      setReleases(data.releases || [])

      if (data.releases && data.releases.length > 0) {
        setLatestVersion(data.releases[0].version)
      }

      // Get next version
      fetchNextVersion('patch')
    } catch (error) {
      console.error('Failed to fetch releases:', error)
    }
  }

  const fetchNextVersion = async (type: string) => {
    try {
      const response = await fetch(`/api/console/releases/${skillId}/next-version?bump=${type}`)
      const data = await response.json()
      setNextVersion(data.next_version)
    } catch (error) {
      console.error('Failed to fetch next version:', error)
    }
  }

  const fetchChangelog = async () => {
    try {
      const response = await fetch(`/api/console/releases/${skillId}/changelog`)
      const data = await response.json()
      setChangelog(data.changelog)
    } catch (error) {
      console.error('Failed to fetch changelog:', error)
    }
  }

  const handleCreateRelease = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const formData = new FormData(e.currentTarget)

    try {
      const response = await fetch(`/api/console/releases/${skillId}/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: formData.get('version'),
          description: formData.get('description'),
          author: formData.get('author') || 'system',
          changes: (formData.get('changes') as string)?.split('\n').filter(Boolean),
          breaking: formData.get('breaking') === 'on',
        })
      })

      if (response.ok) {
        setShowCreateForm(false)
        fetchReleases()
      }
    } catch (error) {
      console.error('Failed to create release:', error)
    }
  }

  const viewReleaseNotes = async (version: string) => {
    try {
      const response = await fetch(`/api/console/releases/${skillId}/${version}/notes`)
      const data = await response.json()
      // Show in modal or panel
      alert(data.notes)
    } catch (error) {
      console.error('Failed to fetch notes:', error)
    }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
        <div className="flex justify-between items-center mb-4">
          <div className="flex items-center gap-3">
            <Package className="text-blue-600" size={24} />
            <div>
              <h2 className="text-xl font-bold text-slate-900 dark:text-white">Release Manager</h2>
              <p className="text-sm text-slate-600 dark:text-slate-400">Skill: {skillId}</p>
            </div>
          </div>
          <button
            onClick={() => setShowCreateForm(!showCreateForm)}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg font-medium hover:bg-green-700"
          >
            <Plus size={18} />
            New Release
          </button>
        </div>

        {/* Version Info */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-slate-600 dark:text-slate-400">Latest Version</p>
            <p className="text-2xl font-bold text-slate-900 dark:text-white">
              {latestVersion || 'None'}
            </p>
          </div>
          <div>
            <p className="text-xs text-slate-600 dark:text-slate-400">Next Version ({bumpType})</p>
            <p className="text-2xl font-bold text-blue-600">{nextVersion}</p>
          </div>
        </div>
      </div>

      {/* Create Release Form */}
      {showCreateForm && (
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
          <h3 className="text-lg font-bold text-slate-900 dark:text-white mb-4">Create New Release</h3>

          <form onSubmit={handleCreateRelease} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                  Version (Semantic)
                </label>
                <input
                  type="text"
                  name="version"
                  placeholder={nextVersion || '1.0.0'}
                  defaultValue={nextVersion || ''}
                  className="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                  required
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                  Author
                </label>
                <input
                  type="text"
                  name="author"
                  placeholder="Your name"
                  className="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                Description
              </label>
              <textarea
                name="description"
                placeholder="Release description"
                className="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
                rows={2}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                Changes (one per line)
              </label>
              <textarea
                name="changes"
                placeholder="- Feature A added&#10;- Bug fix B&#10;- Performance improvement"
                className="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white font-mono text-xs"
                rows={4}
              />
            </div>

            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                name="breaking"
                className="rounded"
              />
              <span className="text-sm text-slate-700 dark:text-slate-300">
                This is a breaking change
              </span>
            </label>

            <div className="flex gap-2">
              <button
                type="submit"
                className="flex-1 px-4 py-2 bg-green-600 text-white rounded-lg font-medium hover:bg-green-700"
              >
                Create Release
              </button>
              <button
                type="button"
                onClick={() => setShowCreateForm(false)}
                className="px-4 py-2 bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-white rounded-lg font-medium"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Changelog Link */}
      {releases.length > 0 && (
        <button
          onClick={() => changelog ? setChangelog(null) : fetchChangelog()}
          className="w-full px-4 py-2 text-left bg-slate-100 dark:bg-slate-700 text-slate-900 dark:text-white rounded-lg font-medium hover:bg-slate-200 dark:hover:bg-slate-600"
        >
          {changelog ? '📖 Hide Changelog' : '📖 View Changelog'}
        </button>
      )}

      {changelog && (
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
          <pre className="bg-slate-100 dark:bg-slate-900/50 p-4 rounded overflow-auto text-xs">
            {changelog}
          </pre>
        </div>
      )}

      {/* Release History */}
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
        <div className="p-4 border-b border-slate-200 dark:border-slate-700 font-bold text-slate-900 dark:text-white">
          Release History ({releases.length})
        </div>

        {releases.length === 0 ? (
          <div className="p-8 text-center text-slate-600 dark:text-slate-400">
            No releases yet. Create the first one!
          </div>
        ) : (
          <div className="divide-y divide-slate-200 dark:divide-slate-700">
            {releases.map((release) => (
              <div
                key={release.version}
                className="p-4 hover:bg-slate-50 dark:hover:bg-slate-900/30 cursor-pointer transition"
              >
                <div className="flex justify-between items-start mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-slate-900 dark:text-white">v{release.version}</span>
                    {release.breaking_changes && (
                      <span className="text-xs px-2 py-1 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded">
                        BREAKING
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-slate-500">
                    {new Date(release.timestamp).toLocaleDateString()}
                  </span>
                </div>

                <p className="text-sm text-slate-700 dark:text-slate-300 mb-2">
                  {release.description}
                </p>

                <p className="text-xs text-slate-600 dark:text-slate-400">
                  By {release.author} • {release.changes.length} changes
                </p>

                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    viewReleaseNotes(release.version)
                  }}
                  className="text-xs text-blue-600 dark:text-blue-400 hover:underline mt-1"
                >
                  View Notes →
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Info */}
      <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
        <p className="text-sm text-blue-700 dark:text-blue-400">
          💡 <strong>Semantic Versioning:</strong> Use MAJOR.MINOR.PATCH format.
          Major version for breaking changes, minor for new features, patch for bug fixes.
        </p>
      </div>
    </div>
  )
}
