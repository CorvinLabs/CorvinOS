/**
 * Custom Repository Card — ADR-0451/0454 Week 2
 * Displays a custom repository with status and action buttons
 *
 * Features:
 * - Status indicator (healthy, loading, error)
 * - Extension count display
 * - Error message display (per ADR-0453 error taxonomy)
 * - Action buttons: Refresh, Disable, Remove
 * - Accessible card design
 */

import { useState } from 'react'
import { AlertCircle, CheckCircle2, RotateCw, Trash2, Eye, EyeOff, ExternalLink } from 'lucide-react'
import { cn, formatRelativeToNow } from '@/lib/utils'

interface CustomRepositoryCardProps {
  repoUrl: string
  status: 'healthy' | 'loading' | 'error'
  extensionCount: number
  errorMessage?: string
  lastChecked?: string
  enabled?: boolean
  onRefresh?: () => Promise<void>
  onToggle?: () => Promise<void>
  onRemove?: () => Promise<void>
  className?: string
}

export function CustomRepositoryCard({
  repoUrl,
  status,
  extensionCount,
  errorMessage,
  lastChecked,
  enabled = true,
  onRefresh,
  onToggle,
  onRemove,
  className = ''
}: CustomRepositoryCardProps) {
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [isToggling, setIsToggling] = useState(false)
  const [isRemoving, setIsRemoving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const handleRefresh = async () => {
    if (!onRefresh) return
    setIsRefreshing(true)
    setActionError(null)
    try {
      await onRefresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Refresh failed')
    } finally {
      setIsRefreshing(false)
    }
  }

  const handleToggle = async () => {
    if (!onToggle) return
    setIsToggling(true)
    setActionError(null)
    try {
      await onToggle()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Toggle failed')
    } finally {
      setIsToggling(false)
    }
  }

  const handleRemove = async () => {
    if (!onRemove) return
    setIsRemoving(true)
    setActionError(null)
    try {
      await onRemove()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Remove failed')
    } finally {
      setIsRemoving(false)
    }
  }

  const repoOwner = repoUrl.split('/').slice(-2).join('/')

  return (
    <article
      className={cn(
        'border rounded-lg p-4 bg-card',
        status === 'error' && 'border-destructive/50',
        !enabled && 'opacity-60',
        className
      )}
      aria-label={`Repository: ${repoUrl}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <a
              href={repoUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-primary hover:underline truncate flex items-center gap-1"
              title={repoUrl}
            >
              {repoOwner}
              <ExternalLink className="h-3 w-3 flex-shrink-0" />
            </a>
            {!enabled && (
              <span className="inline-block px-2 py-0.5 bg-muted text-muted-foreground text-xs rounded">
                Disabled
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground truncate">{repoUrl}</p>
        </div>

        {/* Status Indicator */}
        <div className="flex-shrink-0">
          {status === 'healthy' && (
            <CheckCircle2 className="h-5 w-5 text-green-500" aria-label="Healthy" />
          )}
          {status === 'loading' && (
            <RotateCw className="h-5 w-5 animate-spin text-blue-500" aria-label="Loading" />
          )}
          {status === 'error' && (
            <AlertCircle className="h-5 w-5 text-destructive" aria-label="Error" />
          )}
        </div>
      </div>

      {/* Extension Count */}
      <p className="text-sm text-foreground mb-2">
        <strong>{extensionCount}</strong> extension{extensionCount !== 1 ? 's' : ''}
      </p>

      {/* Error Message */}
      {errorMessage && (
        <div className="mb-3 p-2 bg-destructive/10 border border-destructive/30 rounded text-sm text-destructive flex gap-2">
          <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
          <p>{errorMessage}</p>
        </div>
      )}

      {/* Last Checked */}
      {lastChecked && (
        <p className="text-xs text-muted-foreground mb-3">
          Last checked {formatRelativeToNow(lastChecked)}
        </p>
      )}

      {/* Action Error */}
      {actionError && (
        <div className="mb-2 p-2 bg-destructive/10 border border-destructive/30 rounded text-xs text-destructive">
          {actionError}
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex gap-2 flex-wrap">
        <button
          onClick={handleRefresh}
          disabled={isRefreshing}
          className={cn(
            'flex items-center gap-1 px-3 py-1.5 text-sm rounded',
            'border border-input bg-background hover:bg-accent',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            'transition-colors'
          )}
          aria-label="Refresh repository metadata"
        >
          <RotateCw className={cn('h-4 w-4', isRefreshing && 'animate-spin')} />
          {isRefreshing ? 'Refreshing...' : 'Refresh'}
        </button>

        {onToggle && (
          <button
            onClick={handleToggle}
            disabled={isToggling}
            className={cn(
              'flex items-center gap-1 px-3 py-1.5 text-sm rounded',
              'border border-input bg-background hover:bg-accent',
              'disabled:opacity-50 disabled:cursor-not-allowed',
              'transition-colors'
            )}
            aria-label={enabled ? 'Disable repository' : 'Enable repository'}
          >
            {enabled ? (
              <>
                <Eye className="h-4 w-4" />
                {isToggling ? 'Disabling...' : 'Disable'}
              </>
            ) : (
              <>
                <EyeOff className="h-4 w-4" />
                {isToggling ? 'Enabling...' : 'Enable'}
              </>
            )}
          </button>
        )}

        {onRemove && (
          <button
            onClick={handleRemove}
            disabled={isRemoving}
            className={cn(
              'flex items-center gap-1 px-3 py-1.5 text-sm rounded',
              'border border-destructive/50 bg-background hover:bg-destructive/10 text-destructive',
              'disabled:opacity-50 disabled:cursor-not-allowed',
              'transition-colors'
            )}
            aria-label="Remove repository"
          >
            <Trash2 className="h-4 w-4" />
            {isRemoving ? 'Removing...' : 'Remove'}
          </button>
        )}
      </div>
    </article>
  )
}
