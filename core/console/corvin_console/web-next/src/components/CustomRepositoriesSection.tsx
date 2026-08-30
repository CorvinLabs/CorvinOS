/**
 * Custom Repositories Section — ADR-0451/0454 Week 2
 * Wraps CustomRepositoryForm + CustomRepositoryCard list
 * Managed by useCustomRepositories hook
 */

import React from 'react'
import { Plus, AlertCircle } from 'lucide-react'
import { CustomRepositoryForm } from '@/components/CustomRepositoryForm'
import { CustomRepositoryCard } from '@/components/CustomRepositoryCard'
import { useCustomRepositories } from '@/hooks/useCustomRepositories'
import { cn } from '@/lib/utils'

interface CustomRepositoriesSectionProps {
  className?: string
}

export function CustomRepositoriesSection({ className = '' }: CustomRepositoriesSectionProps) {
  const {
    repositories,
    loading,
    error,
    refetch,
    refresh,
    toggle,
    remove
  } = useCustomRepositories()

  const [showForm, setShowForm] = React.useState(false)

  const handleRepositoryAdded = () => {
    setShowForm(false)
    refetch()
  }

  return (
    <section className={cn('space-y-4', className)} aria-label="Custom GitHub Repositories">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <Plus className="h-5 w-5" />
            Custom Repositories
          </h3>
          <p className="text-sm text-muted-foreground mt-1">
            Add private or custom GitHub repositories to discover plugins
          </p>
        </div>
        {!showForm && (
          <button
            onClick={() => setShowForm(true)}
            className="px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors text-sm font-medium"
            aria-label="Add custom repository"
          >
            Add Repository
          </button>
        )}
      </div>

      {/* Error Alert */}
      {error && (
        <div className="p-3 bg-destructive/10 border border-destructive rounded-md flex gap-2">
          <AlertCircle className="h-5 w-5 text-destructive flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-destructive">Failed to load repositories</p>
            <p className="text-xs text-destructive mt-1">{error}</p>
            <button
              onClick={() => refetch()}
              className="text-xs underline hover:no-underline mt-2"
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {/* Add Form */}
      {showForm && (
        <div className="border rounded-lg p-4 bg-muted/50">
          <div className="flex justify-between items-center mb-4">
            <h4 className="font-medium">Add Custom Repository</h4>
            <button
              onClick={() => setShowForm(false)}
              className="text-sm text-muted-foreground hover:text-foreground"
              aria-label="Close form"
            >
              ✕
            </button>
          </div>
          <CustomRepositoryForm onRepositoryAdded={handleRepositoryAdded} />
        </div>
      )}

      {/* Loading State */}
      {loading && (
        <div className="text-center py-8">
          <p className="text-muted-foreground">Loading repositories...</p>
        </div>
      )}

      {/* Empty State */}
      {!loading && repositories.length === 0 && (
        <div className="text-center py-12 border rounded-lg border-dashed">
          <p className="text-muted-foreground mb-3">No custom repositories yet</p>
          {!showForm && (
            <button
              onClick={() => setShowForm(true)}
              className="text-sm text-primary hover:underline"
            >
              Add your first repository
            </button>
          )}
        </div>
      )}

      {/* Repository List */}
      {!loading && repositories.length > 0 && (
        <div className="space-y-3">
          {repositories.map(repo => (
            <CustomRepositoryCard
              key={repo.repo_url}
              repoUrl={repo.repo_url}
              status={repo.status}
              extensionCount={repo.extension_count}
              errorMessage={repo.error_message}
              lastChecked={repo.last_checked}
              enabled={repo.enabled ?? true}
              onRefresh={() => refresh(repo.repo_url)}
              onToggle={() => toggle(repo.repo_url)}
              onRemove={() => remove(repo.repo_url)}
            />
          ))}
        </div>
      )}
    </section>
  )
}
