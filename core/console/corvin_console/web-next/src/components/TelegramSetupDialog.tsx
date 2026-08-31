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
import { CheckCircle, AlertCircle, ExternalLink, Loader, X } from 'lucide-react'

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
      <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-gradient-to-r from-sky-600 to-blue-600 text-white px-6 py-4 flex items-start justify-between">
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
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Bot Token von @BotFather
                </label>
                <textarea
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="123456789:AA..."
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm font-mono focus:outline-none focus:ring-2 focus:ring-sky-500"
                  rows={3}
                />
                <p className="text-xs text-gray-500 mt-2">
                  Token bekommst du so: In Telegram @BotFather öffnen → /newbot senden
                  (oder /token für einen bestehenden Bot) → Token kopieren
                </p>
              </div>

              <button
                onClick={handleValidate}
                className="w-full bg-sky-600 text-white py-2 rounded-md font-medium hover:bg-sky-700 transition"
              >
                Validieren &amp; Weiter
              </button>
            </>
          )}

          {/* Step 2: Validating */}
          {step === 'validating' && (
            <div className="flex items-center justify-center py-8">
              <Loader className="w-8 h-8 animate-spin text-sky-600 mr-3" />
              <span>Validiere Token...</span>
            </div>
          )}

          {/* Step 3: Confirm */}
          {step === 'confirm' && validationResult && (
            <>
              <div className="bg-green-50 border border-green-200 rounded-md p-4">
                <div className="flex items-center">
                  <CheckCircle className="w-5 h-5 text-green-600 mr-2" />
                  <span className="text-sm font-medium text-green-800">Token validiert ✓</span>
                </div>
                <p className="text-sm text-green-700 mt-2">
                  Bot: <strong>{validationResult.botName}</strong>{' '}
                  (@{validationResult.botUsername}, ID {validationResult.botId})
                </p>
              </div>

              <div className="bg-blue-50 border border-blue-200 rounded-md p-3">
                <p className="text-xs text-blue-800">
                  💡 Telegram-Bots brauchen keine zusätzliche Autorisierung: nach dem
                  Speichern kannst du dem Bot direkt schreiben (@{validationResult.botUsername}).
                </p>
              </div>

              <div className="border-t pt-4">
                <p className="text-sm text-gray-600 mb-3">
                  Der Token wird lokal gespeichert (chmod 600, nie im Klartext angezeigt):
                </p>
                <button
                  onClick={handleSaveToken}
                  className="w-full bg-green-600 text-white py-2 rounded-md font-medium hover:bg-green-700 transition"
                >
                  Token speichern &amp; Setup abschließen
                </button>
              </div>
            </>
          )}

          {/* Step 4: Saving */}
          {step === 'saving' && (
            <div className="flex items-center justify-center py-8">
              <Loader className="w-8 h-8 animate-spin text-green-600 mr-3" />
              <span>Speichern...</span>
            </div>
          )}

          {/* Step 5: Success */}
          {step === 'success' && (
            <div className="text-center py-6">
              <div className="flex justify-center mb-4">
                <CheckCircle className="w-12 h-12 text-green-600" />
              </div>
              <h3 className="text-lg font-semibold text-gray-900 mb-2">Bot erfolgreich aktiviert! 🎉</h3>
              <p className="text-sm text-gray-600 mb-4">
                Dein Telegram-Bot ist jetzt bereit. Der Daemon startet neu und verbindet sich mit Telegram.
              </p>
              <p className="text-xs text-gray-500 mb-4">
                Falls der Bot nicht sofort antwortet, kann es 30 Sekunden dauern bis die Verbindung hergestellt ist.
              </p>
              <button
                onClick={() => (onSuccess ? onSuccess() : window.location.reload())}
                className="px-6 py-2 bg-sky-600 text-white rounded-md hover:bg-sky-700 transition font-medium"
              >
                {onSuccess ? 'Weiter' : 'Schließen & Neu laden'}
              </button>
            </div>
          )}

          {/* Step: Error */}
          {step === 'error' && (
            <>
              <div className="bg-red-50 border border-red-200 rounded-md p-4">
                <div className="flex items-start">
                  <AlertCircle className="w-5 h-5 text-red-600 mr-2 mt-0.5 flex-shrink-0" />
                  <div>
                    <h3 className="font-medium text-red-800">Fehler</h3>
                    <p className="text-sm text-red-700 mt-1">{error}</p>
                  </div>
                </div>
              </div>

              <button
                onClick={() => {
                  setStep('input')
                  setToken('')
                  setValidationResult(null)
                  setError('')
                }}
                className="w-full bg-gray-600 text-white py-2 rounded-md font-medium hover:bg-gray-700 transition"
              >
                Nochmal probieren
              </button>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="bg-gray-50 border-t px-6 py-4 text-xs text-gray-500">
          <p>
            Anleitung:{' '}
            <a
              href="https://core.telegram.org/bots#how-do-i-create-a-bot"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sky-600 hover:underline inline-flex items-center"
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
