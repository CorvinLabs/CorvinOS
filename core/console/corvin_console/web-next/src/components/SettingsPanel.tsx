/**
 * SettingsPanel — Configure marketplace plugin settings (pre/post-install)
 *
 * Phase 3: Pre-install settings (optional UI), Post-install settings (sync with PluginsPage)
 * Live-sync ensures settings in Marketplace match PluginsPage settings
 */

import React, { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Save, X } from 'lucide-react'

interface SettingsPanelProps {
  pluginId: string
  initialSettings?: Record<string, unknown>
  schema?: Record<string, unknown>
  onSave?: (settings: Record<string, unknown>) => Promise<void>
  onClose?: () => void
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  pluginId,
  initialSettings = {},
  schema,
  onSave,
  onClose,
}) => {
  const queryClient = useQueryClient()
  // The schema-driven form is a Phase 3 stub (see the placeholder below), so
  // nothing mutates this yet — the setter returns with the form.
  const [settings] = useState<Record<string, unknown>>(initialSettings)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const handleSave = async () => {
    try {
      setSaving(true)
      setError(null)
      setSuccess(false)

      if (onSave) {
        await onSave(settings)
      }

      // Invalidate plugins query to sync with PluginsPage
      queryClient.invalidateQueries({ queryKey: ['plugins'] })

      setSuccess(true)
      setTimeout(() => {
        if (onClose) onClose()
      }, 1000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-700 p-6 max-w-2xl">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
          Settings for {pluginId}
        </h3>
        {onClose && (
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* Settings form (stub — would dynamically render based on schema) */}
      {schema ? (
        <div className="space-y-4 mb-6">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Schema-based settings form would render here (stub for Phase 3)
          </p>
          {/* TODO: Render form fields based on schema */}
        </div>
      ) : (
        <div className="text-sm text-slate-600 dark:text-slate-400 mb-6">
          No schema provided. Plugin does not require configuration, or settings come from PluginsPage.
        </div>
      )}

      {/* Error message (ADR-0297 compliant — no PII) */}
      {error && (
        <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 rounded">
          <p className="text-red-700 dark:text-red-200 text-sm">{error}</p>
        </div>
      )}

      {/* Success message */}
      {success && (
        <div className="mb-4 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 rounded">
          <p className="text-green-700 dark:text-green-200 text-sm">Settings saved successfully</p>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-slate-400 font-medium flex items-center justify-center gap-2"
        >
          {saving ? (
            <>
              <div className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
              Saving...
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              Save Settings
            </>
          )}
        </button>
        {onClose && (
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 bg-slate-200 dark:bg-slate-800 text-slate-900 dark:text-white rounded-lg hover:bg-slate-300 dark:hover:bg-slate-700 font-medium"
          >
            Cancel
          </button>
        )}
      </div>

      <p className="text-xs text-slate-500 dark:text-slate-400 mt-4">
        Settings are synced with PluginsPage in real-time. Changes here will appear there automatically.
      </p>
    </div>
  )
}

export default SettingsPanel
