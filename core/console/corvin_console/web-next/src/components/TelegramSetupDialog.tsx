/**
 * Telegram Zero-Config Setup Dialog
 *
 * Component flow:
 * 1. Input: User pastes bot token (from @BotFather)
 * 2. Validate: Call /v1/console/telegram/validate-token
 * 3. Confirm: Show the validated bot identity
 * 4. Save: Call /v1/console/telegram/save-token (when confirmed)
 *
 * Note: unlike Discord there is NO authorization URL step — a Telegram bot
 * token from @BotFather is immediately usable once saved.
 */

import { useState } from 'react'
import { CheckCircle, AlertCircle, ExternalLink, Loader2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

interface ValidateTelegramTokenResponse {
  valid: boolean
  botId?: string
  botUsername?: string
  botName?: string
  error?: string
}

interface SaveTokenResponse {
  success: boolean
  error?: string
}

type DialogStep = 'input' | 'validating' | 'confirm' | 'saving' | 'success' | 'error'

/** A non-2xx response's body carries the real error (FastAPI's global
 * exception handler puts it in `detail`) -- reading only response.statusText
 * discarded it and showed a bare "HTTP 500: Internal Server Error" with no
 * way to diagnose the actual failure. */
async function _errorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (body?.detail) return `HTTP ${response.status}: ${body.detail}`
  } catch {
    // body wasn't JSON (or was already consumed) -- fall through
  }
  return `HTTP ${response.status}: ${response.statusText}`
}

interface TelegramSetupDialogProps {
  /** CSRF token of the active console session (session.csrf_token) —
   * the validate/save endpoints are mutations and require x-csrf-token. */
  csrf: string
  /** Close the dialog without saving (backdrop click, X button). When omitted
   * the dialog has no dismiss affordance (legacy full-page usage). */
  onClose?: () => void
  /** Called after a successful save instead of the window.location.reload()
   * fallback, so an embedding flow (onboarding) can advance in-place. */
  onSuccess?: () => void
}

export function TelegramSetupDialog({ csrf, onClose, onSuccess }: TelegramSetupDialogProps) {
  const [step, setStep] = useState<DialogStep>('input')
  const [token, setToken] = useState('')
  const [validationResult, setValidationResult] = useState<ValidateTelegramTokenResponse | null>(null)
  const [error, setError] = useState('')

  const handleValidate = async () => {
    if (!token.trim()) {
      setError('Token erforderlich')
      return
    }

    setStep('validating')
    setError('')

    try {
      const response = await fetch('/v1/console/telegram/validate-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-csrf-token': csrf },
        body: JSON.stringify({ token: token.trim() }),
      })

      if (!response.ok) {
        setError(await _errorDetail(response))
        setStep('error')
        return
      }

      const data: ValidateTelegramTokenResponse = await response.json()

      if (!data.valid) {
        setError(data.error || 'Token ungültig')
        setStep('error')
        return
      }

      setValidationResult(data)
      setStep('confirm')
    } catch (err) {
      setError(`Fehler: ${err instanceof Error ? err.message : 'Unbekannt'}`)
      setStep('error')
    }
  }

  const handleSaveToken = async () => {
    setStep('saving')
    setError('')

    try {
      const response = await fetch('/v1/console/telegram/save-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-csrf-token': csrf },
        body: JSON.stringify({ token: token.trim() }),
      })

      if (!response.ok) {
        setError(await _errorDetail(response))
        setStep('error')
        return
      }

      const data: SaveTokenResponse = await response.json()

      if (!data.success) {
        setError(data.error || 'Speichern fehlgeschlagen')
        setStep('error')
        return
      }

      setStep('success')
    } catch (err) {
      setError(`Fehler: ${err instanceof Error ? err.message : 'Unbekannt'}`)
      setStep('error')
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose ? (e) => { if (e.target === e.currentTarget) onClose() } : undefined}
    >
      <div className="bg-card border border-border rounded-lg shadow-2xl shadow-black/30 max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header — Telegram brand blue is intentional here (third-party service identity, not app chrome) */}
        <div className="sticky top-0 bg-sky-600 dark:bg-sky-700 text-white px-6 py-4 flex items-start justify-between">
          <div>
            <h2 className="text-xl font-bold">🤖 Telegram Bot Aktivierung</h2>
            <p className="text-sm text-sky-100 mt-1">Nur 2 Schritte bis der Bot einsatzbereit ist</p>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              disabled={step === 'validating' || step === 'saving'}
              className="text-sky-100 hover:text-white disabled:opacity-40"
              aria-label="Schließen"
            >
              <X className="w-5 h-5" />
            </button>
          )}
        </div>

        {/* Content */}
        <div className="p-6 space-y-4">
          {/* Step 1: Input Token */}
          {step === 'input' && (
            <>
              <div>
                <label className="block text-sm font-medium text-foreground mb-2">
                  Bot Token von @BotFather
                </label>
                <Textarea
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="123456789:AA..."
                  rows={3}
                />
                <p className="text-xs text-muted-foreground mt-2">
                  Token bekommst du so: In Telegram @BotFather öffnen → /newbot senden
                  (oder /token für einen bestehenden Bot) → Token kopieren
                </p>
              </div>

              <Button onClick={handleValidate} className="w-full" variant="accent">
                Validieren &amp; Weiter
              </Button>
            </>
          )}

          {/* Step 2: Validating */}
          {step === 'validating' && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-8 h-8 animate-spin text-accent mr-3" />
              <span className="text-foreground">Validiere Token...</span>
            </div>
          )}

          {/* Step 3: Confirm */}
          {step === 'confirm' && validationResult && (
            <>
              <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-md p-4">
                <div className="flex items-center">
                  <CheckCircle className="w-5 h-5 text-emerald-600 dark:text-emerald-400 mr-2" />
                  <span className="text-sm font-medium text-emerald-700 dark:text-emerald-400">Token validiert ✓</span>
                </div>
                <p className="text-sm text-emerald-700 dark:text-emerald-400 mt-2">
                  Bot: <strong>{validationResult.botName}</strong>{' '}
                  (@{validationResult.botUsername}, ID {validationResult.botId})
                </p>
              </div>

              <div className="bg-muted/40 border border-border rounded-md p-3">
                <p className="text-xs text-muted-foreground">
                  💡 Telegram-Bots brauchen keine zusätzliche Autorisierung: nach dem
                  Speichern kannst du dem Bot direkt schreiben (@{validationResult.botUsername}).
                </p>
              </div>

              <div className="border-t border-border pt-4">
                <p className="text-sm text-muted-foreground mb-3">
                  Der Token wird lokal gespeichert (chmod 600, nie im Klartext angezeigt):
                </p>
                <Button onClick={handleSaveToken} className="w-full" variant="accent">
                  Token speichern &amp; Setup abschließen
                </Button>
              </div>
            </>
          )}

          {/* Step 4: Saving */}
          {step === 'saving' && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-8 h-8 animate-spin text-emerald-600 dark:text-emerald-400 mr-3" />
              <span className="text-foreground">Speichern...</span>
            </div>
          )}

          {/* Step 5: Success */}
          {step === 'success' && (
            <div className="text-center py-6">
              <div className="flex justify-center mb-4">
                <CheckCircle className="w-12 h-12 text-emerald-600 dark:text-emerald-400" />
              </div>
              <h3 className="text-lg font-semibold text-foreground mb-2">Bot erfolgreich aktiviert! 🎉</h3>
              <p className="text-sm text-muted-foreground mb-4">
                Dein Telegram-Bot ist jetzt bereit. Der Daemon startet neu und verbindet sich mit Telegram.
              </p>
              <p className="text-xs text-muted-foreground mb-4">
                Falls der Bot nicht sofort antwortet, kann es 30 Sekunden dauern bis die Verbindung hergestellt ist.
              </p>
              <Button onClick={() => (onSuccess ? onSuccess() : window.location.reload())} variant="accent">
                {onSuccess ? 'Weiter' : 'Schließen & Neu laden'}
              </Button>
            </div>
          )}

          {/* Step: Error */}
          {step === 'error' && (
            <>
              <div className="bg-destructive/10 border border-destructive/40 rounded-md p-4">
                <div className="flex items-start">
                  <AlertCircle className="w-5 h-5 text-destructive mr-2 mt-0.5 flex-shrink-0" />
                  <div>
                    <h3 className="font-medium text-destructive">Fehler</h3>
                    <p className="text-sm text-destructive mt-1">{error}</p>
                  </div>
                </div>
              </div>

              <Button
                onClick={() => {
                  setStep('input')
                  setToken('')
                  setValidationResult(null)
                  setError('')
                }}
                className="w-full"
                variant="secondary"
              >
                Nochmal probieren
              </Button>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="bg-muted/30 border-t border-border px-6 py-4 text-xs text-muted-foreground">
          <p>
            Anleitung:{' '}
            <a
              href="https://core.telegram.org/bots#how-do-i-create-a-bot"
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline inline-flex items-center"
            >
              Telegram-Bot-Doku öffnen
              <ExternalLink className="w-3 h-3 ml-1" />
            </a>
            {' — '}
            @BotFather {' → '} /newbot {' → '} Token kopieren
          </p>
        </div>
      </div>
    </div>
  )
}
