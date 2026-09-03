/**
 * Custom Repository Form — ADR-0451/0452/0454 Week 2
 * Allows users to add custom GitHub repositories to the marketplace
 *
 * Features:
 * - URL input with real-time validation (POST /validate, 300ms debounce)
 * - Optional GitHub token selection
 * - Error message display per ADR-0453 error taxonomy
 * - Accessible form controls (ARIA labels, semantic HTML)
 */

import React, { useState, useCallback, useRef, useEffect } from 'react'
import { AlertCircle, CheckCircle2, Loader } from 'lucide-react'
import { cn } from '@/lib/utils'
import { BASE } from '@/lib/api/client'
import { useAuth } from '@/lib/auth'

interface CustomRepositoryFormProps {
  onRepositoryAdded?: (url: string) => void
  className?: string
}

interface ValidationState {
  status: 'idle' | 'validating' | 'valid' | 'invalid'
  error?: string
}

const GITHUB_URL_PATTERN = /^https:\/\/github\.com\/[a-zA-Z0-9_-]+\/[a-zA-Z0-9_.-]+\/?$/
const DEBOUNCE_MS = 300

export function CustomRepositoryForm({
  onRepositoryAdded,
  className = ''
}: CustomRepositoryFormProps) {
  // validate / add are POSTs → CSRF token (backend: require_csrf).
  const { session } = useAuth()
  const csrf = session?.csrf_token ?? ''
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [validation, setValidation] = useState<ValidationState>({ status: 'idle' })
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const debounceTimerRef = useRef<NodeJS.Timeout>()
  const isMountedRef = useRef(true)

  useEffect(() => {
    return () => {
      isMountedRef.current = false
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current)
    }
  }, [])

  // Real-time validation via POST /validate, debounced 300ms
  const validateUrl = useCallback(async (repositoryUrl: string) => {
    if (!repositoryUrl.trim()) {
      setValidation({ status: 'idle' })
      return
    }

    // Client-side format check first (fail-fast)
    if (!GITHUB_URL_PATTERN.test(repositoryUrl)) {
      setValidation({
        status: 'invalid',
        error: 'Invalid URL. Expected: https://github.com/owner/repo'
      })
      return
    }

    setValidation({ status: 'validating' })

    try {
      const response = await fetch(`${BASE}/api/v1/marketplace/custom-repositories/validate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({
          repo_url: repositoryUrl,
          token_ref: token || null
        })
      })

      if (!isMountedRef.current) return

      if (response.ok) {
        setValidation({ status: 'valid' })
      } else {
        const errorData = await response.json()
        setValidation({
          status: 'invalid',
          error: errorData.error_message || 'Repository validation failed'
        })
      }
    } catch (err) {
      if (isMountedRef.current) {
        setValidation({
          status: 'invalid',
          error: err instanceof Error ? err.message : 'Network error during validation'
        })
      }
    }
  }, [token, csrf])

  // Debounced onChange handler
  const handleUrlChange = useCallback((value: string) => {
    setUrl(value)

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
    }

    debounceTimerRef.current = setTimeout(() => {
      validateUrl(value)
    }, DEBOUNCE_MS)
  }, [validateUrl])

  // Submit handler: POST /add-repository
  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault()

    if (validation.status !== 'valid') {
      setSubmitError('Please provide a valid repository URL')
      return
    }

    setIsSubmitting(true)
    setSubmitError(null)

    try {
      const response = await fetch(`${BASE}/api/v1/marketplace/custom-repositories`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({
          repo_url: url,
          token_ref: token || null
        })
      })

      if (!isMountedRef.current) return

      if (response.ok) {
        setUrl('')
        setToken('')
        setValidation({ status: 'idle' })
        onRepositoryAdded?.(url)
      } else {
        const errorData = await response.json()
        setSubmitError(errorData.error_message || 'Failed to add repository')
      }
    } catch (err) {
      if (isMountedRef.current) {
        setSubmitError(err instanceof Error ? err.message : 'Network error')
      }
    } finally {
      if (isMountedRef.current) setIsSubmitting(false)
    }
  }, [url, token, validation, onRepositoryAdded, csrf])

  const isSubmitEnabled = validation.status === 'valid' && !isSubmitting

  return (
    <form
      onSubmit={handleSubmit}
      className={cn('space-y-4 p-4 border rounded-lg bg-card', className)}
      aria-label="Add custom repository"
    >
      {/* URL Input */}
      <div>
        <label
          htmlFor="repo-url"
          className="block text-sm font-medium text-foreground mb-2"
        >
          Repository URL
          <span className="text-destructive ml-1">*</span>
        </label>
        <div className="relative">
          <input
            id="repo-url"
            type="url"
            value={url}
            onChange={(e) => handleUrlChange(e.target.value)}
            placeholder="https://github.com/owner/repo"
            className={cn(
              'w-full px-3 py-2 border rounded-md bg-background text-foreground',
              'placeholder:text-muted-foreground',
              'focus:outline-none focus:ring-2 focus:ring-primary',
              validation.status === 'valid' && 'border-green-500',
              validation.status === 'invalid' && 'border-destructive'
            )}
            aria-describedby={validation.error ? 'url-error' : undefined}
            aria-label="GitHub repository URL"
            required
          />
          {validation.status === 'validating' && (
            <Loader className="absolute right-3 top-2.5 h-5 w-5 animate-spin text-primary" />
          )}
          {validation.status === 'valid' && (
            <CheckCircle2 className="absolute right-3 top-2.5 h-5 w-5 text-green-500" />
          )}
        </div>
        {validation.error && (
          <p id="url-error" className="text-sm text-destructive mt-1 flex items-center gap-1">
            <AlertCircle className="h-4 w-4" />
            {validation.error}
          </p>
        )}
      </div>

      {/* Optional Token Input */}
      <div>
        <label
          htmlFor="repo-token"
          className="block text-sm font-medium text-foreground mb-2"
        >
          GitHub Token <span className="text-muted-foreground">(optional)</span>
        </label>
        <input
          id="repo-token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="ghp_... (private repos only)"
          className={cn(
            'w-full px-3 py-2 border rounded-md bg-background text-foreground',
            'placeholder:text-muted-foreground',
            'focus:outline-none focus:ring-2 focus:ring-primary'
          )}
          aria-label="Optional GitHub Personal Access Token"
        />
        <p className="text-xs text-muted-foreground mt-1">
          Required for private repositories. Token is encrypted and never logged.
        </p>
      </div>

      {/* Submit Error */}
      {submitError && (
        <div className="p-3 bg-destructive/10 border border-destructive rounded-md flex gap-2">
          <AlertCircle className="h-5 w-5 text-destructive flex-shrink-0 mt-0.5" />
          <p className="text-sm text-destructive">{submitError}</p>
        </div>
      )}

      {/* Submit Button */}
      <button
        type="submit"
        disabled={!isSubmitEnabled}
        className={cn(
          'w-full px-4 py-2 rounded-md font-medium',
          'transition-colors duration-200',
          isSubmitEnabled
            ? 'bg-primary text-primary-foreground hover:bg-primary/90 cursor-pointer'
            : 'bg-muted text-muted-foreground cursor-not-allowed opacity-50'
        )}
        aria-busy={isSubmitting}
      >
        {isSubmitting ? (
          <span className="flex items-center justify-center gap-2">
            <Loader className="h-4 w-4 animate-spin" />
            Adding...
          </span>
        ) : (
          'Add Repository'
        )}
      </button>
    </form>
  )
}
