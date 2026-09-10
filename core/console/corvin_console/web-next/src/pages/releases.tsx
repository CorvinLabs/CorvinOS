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

import { useState, useEffect, useCallback } from 'react'
import { Package, Plus, BookOpen } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'

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

  const fetchNextVersion = useCallback(async (type: string) => {
    try {
      const response = await fetch(`/api/console/releases/${skillId}/next-version?bump=${type}`)
      const data = await response.json()
      setNextVersion(data.next_version)
    } catch (error) {
      console.error('Failed to fetch next version:', error)
    }
  }, [skillId])

  const fetchReleases = useCallback(async () => {
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
  }, [skillId, fetchNextVersion])

  // Load releases on mount / when the skill changes
  useEffect(() => {
    fetchReleases()
  }, [fetchReleases])

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
      <Card>
        <CardHeader>
          <div className="flex justify-between items-center gap-3">
            <div className="flex items-center gap-3">
              <Package className="h-6 w-6 text-accent" />
              <div>
                <CardTitle>Release Manager</CardTitle>
                <CardDescription>Skill: {skillId}</CardDescription>
              </div>
            </div>
            <Button variant="accent" onClick={() => setShowCreateForm(!showCreateForm)}>
              <Plus size={18} />
              New Release
            </Button>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-muted-foreground">Latest Version</p>
            <p className="text-2xl font-bold text-foreground">
              {latestVersion || 'None'}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Next Version ({bumpType})</p>
            <p className="text-2xl font-bold text-accent">{nextVersion}</p>
          </div>
        </CardContent>
      </Card>

      {/* Create Release Form */}
      {showCreateForm && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Create New Release</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreateRelease} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="rel-version">Version (Semantic)</Label>
                  <Input
                    id="rel-version"
                    type="text"
                    name="version"
                    placeholder={nextVersion || '1.0.0'}
                    defaultValue={nextVersion || ''}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="rel-author">Author</Label>
                  <Input
                    id="rel-author"
                    type="text"
                    name="author"
                    placeholder="Your name"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="rel-description">Description</Label>
                <Textarea
                  id="rel-description"
                  name="description"
                  placeholder="Release description"
                  className="font-sans text-sm"
                  rows={2}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="rel-changes">Changes (one per line)</Label>
                <Textarea
                  id="rel-changes"
                  name="changes"
                  placeholder={"- Feature A added\n- Bug fix B\n- Performance improvement"}
                  rows={4}
                />
              </div>

              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  name="breaking"
                  className="rounded border-input accent-accent"
                />
                <span className="text-sm text-foreground">
                  This is a breaking change
                </span>
              </label>

              <div className="flex gap-2">
                <Button type="submit" variant="accent" className="flex-1">
                  Create Release
                </Button>
                <Button type="button" variant="secondary" onClick={() => setShowCreateForm(false)}>
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {/* Changelog Link */}
      {releases.length > 0 && (
        <Button
          variant="secondary"
          className="w-full justify-start"
          onClick={() => changelog ? setChangelog(null) : fetchChangelog()}
        >
          <BookOpen size={16} />
          {changelog ? 'Hide Changelog' : 'View Changelog'}
        </Button>
      )}

      {changelog && (
        <Card>
          <CardContent className="pt-6">
            <pre className="bg-muted p-4 rounded overflow-auto text-xs">
              {changelog}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* Release History */}
      <Card className="overflow-hidden">
        <div className="p-4 border-b border-border font-bold text-foreground">
          Release History ({releases.length})
        </div>

        {releases.length === 0 ? (
          <div className="p-8 text-center text-muted-foreground">
            No releases yet. Create the first one!
          </div>
        ) : (
          <div className="divide-y divide-border">
            {releases.map((release) => (
              <div
                key={release.version}
                className="p-4 hover:bg-muted/30 cursor-pointer transition-colors"
              >
                <div className="flex justify-between items-start mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-foreground">v{release.version}</span>
                    {release.breaking_changes && (
                      <Badge variant="danger" className="text-xs">BREAKING</Badge>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {new Date(release.timestamp).toLocaleDateString()}
                  </span>
                </div>

                <p className="text-sm text-foreground/90 mb-2">
                  {release.description}
                </p>

                <p className="text-xs text-muted-foreground">
                  By {release.author} • {release.changes.length} changes
                </p>

                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    viewReleaseNotes(release.version)
                  }}
                  className="text-xs text-accent hover:underline mt-1"
                >
                  View Notes →
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Info */}
      <div className="rounded-lg border border-border bg-muted/30 p-4">
        <p className="text-sm text-muted-foreground">
          <strong className="text-foreground">Semantic Versioning:</strong> Use MAJOR.MINOR.PATCH format.
          Major version for breaking changes, minor for new features, patch for bug fixes.
        </p>
      </div>
    </div>
  )
}
