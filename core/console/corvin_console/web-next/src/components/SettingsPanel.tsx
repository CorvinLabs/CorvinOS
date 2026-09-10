/**
 * SettingsPanel — Configure marketplace plugin settings (pre/post-install)
 *
 * Phase 3: Pre-install settings (optional UI), Post-install settings (sync with PluginsPage)
 * Live-sync ensures settings in Marketplace match PluginsPage settings
 */

import React, { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Save, X, Loader2 } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

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
    <Card className="max-w-2xl">
      <CardHeader>
        <div className="flex justify-between items-center">
          <CardTitle className="text-lg">Settings for {pluginId}</CardTitle>
          {onClose && (
            <button
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="w-5 h-5" />
            </button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Settings form (stub — would dynamically render based on schema) */}
        {schema ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Schema-based settings form would render here (stub for Phase 3)
            </p>
            {/* TODO: Render form fields based on schema */}
          </div>
        ) : (
          <div className="text-sm text-muted-foreground">
            No schema provided. Plugin does not require configuration, or settings come from PluginsPage.
          </div>
        )}

        {/* Error message (ADR-0297 compliant — no PII) */}
        {error && (
          <div className="p-3 rounded-lg border border-destructive/40 bg-destructive/10">
            <p className="text-sm text-destructive">{error}</p>
          </div>
        )}

        {/* Success message */}
        {success && (
          <div className="p-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10">
            <p className="text-sm text-emerald-700 dark:text-emerald-400">Settings saved successfully</p>
          </div>
        )}

        {/* Action buttons */}
        <div className="flex gap-3">
          <Button
            variant="accent"
            className="flex-1"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                Save Settings
              </>
            )}
          </Button>
          {onClose && (
            <Button variant="secondary" className="flex-1" onClick={onClose}>
              Cancel
            </Button>
          )}
        </div>

        <p className="text-xs text-muted-foreground">
          Settings are synced with PluginsPage in real-time. Changes here will appear there automatically.
        </p>
      </CardContent>
    </Card>
  )
}

export default SettingsPanel
