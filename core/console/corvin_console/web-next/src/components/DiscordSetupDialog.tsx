/**
 * Discord Zero-Config Setup Dialog
 *
 * Component flow:
 * 1. Input: User pastes bot token
 * 2. Validate: Call /v1/console/discord/validate-token
 * 3. Show OAuth2 URL + Open link
 * 4. Save: Call /v1/console/discord/save-token (when confirmed)
 */

import { useState } from 'react'
import { CheckCircle, AlertCircle, Copy, ExternalLink, Loader, X } from 'lucide-react'

interface ValidateTokenResponse {
  valid: boolean
  appId?: string
  appName?: string
  url?: string
  error?: string
  permissionsHuman?: string[]
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

interface DiscordSetupDialogProps {
  /** CSRF token of the active console session (session.csrf_token) —
   * the validate/save endpoints are mutations and require x-csrf-token. */
  csrf: string
  /** Close the dialog without saving (backdrop click, X button, Cancel).
   * The caller unmounts the dialog; state is not preserved across reopen. */
  onClose: () => void
  /** Optional: called after a successful save, before the reload fallback,
   * so the caller can refresh the bridge list in-place. */
  onSuccess?: () => void
}

export function DiscordSetupDialog({ csrf, onClose, onSuccess }: DiscordSetupDialogProps) {
  const [step, setStep] = useState<DialogStep>('input')
  const [token, setToken] = useState('')
  const [validationResult, setValidationResult] = useState<ValidateTokenResponse | null>(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  const handleValidate = async () => {
    if (!token.trim()) {
      setError('Token erforderlich')
      return
    }

    setStep('validating')
    setError('')

    try {
      const response = await fetch('/v1/console/discord/validate-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-csrf-token': csrf },
        body: JSON.stringify({ token: token.trim() }),
      })

      if (!response.ok) {
        setError(await _errorDetail(response))
        setStep('error')
        return
      }

      const data: ValidateTokenResponse = await response.json()

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
      const response = await fetch('/v1/console/discord/save-token', {
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
      onSuccess?.()
    } catch (err) {
      setError(`Fehler: ${err instanceof Error ? err.message : 'Unbekannt'}`)
      setStep('error')
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // Backdrop / X / Cancel are disabled mid-mutation so a click can't abandon a
  // save that already hit the server; success reloads via its own button.
  const busy = step === 'validating' || step === 'saving'

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={() => { if (!busy) onClose() }}
    >
      <div
        className="bg-white rounded-lg shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 bg-gradient-to-r from-indigo-600 to-purple-600 text-white px-6 py-4 flex items-start justify-between">
          <div>
            <h2 className="text-xl font-bold">🤖 Discord Bot Aktivierung</h2>
            <p className="text-sm text-indigo-100 mt-1">Nur 2 Schritte bis der Bot einsatzbereit ist</p>
          </div>
          <button
            onClick={onClose}
            disabled={busy}
            aria-label="Schließen"
            className="text-white/80 hover:text-white transition disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-4">
          {/* Step 1: Input Token */}
          {step === 'input' && (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Bot Token von Discord Developer Portal
                </label>
                <textarea
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="Paste your bot token here..."
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  rows={3}
                />
                <p className="text-xs text-gray-500 mt-2">
                  Token findest du hier: Discord Developer Portal → Applications → Deine App → Bot → Copy Token
                </p>
              </div>

              <div className="flex gap-2">
                <button
                  onClick={onClose}
                  className="px-4 py-2 rounded-md font-medium border border-gray-300 text-gray-700 hover:bg-gray-50 transition"
                >
                  Abbrechen
                </button>
                <button
                  onClick={handleValidate}
                  className="flex-1 bg-indigo-600 text-white py-2 rounded-md font-medium hover:bg-indigo-700 transition"
                >
                  Validieren &amp; Weiter
                </button>
              </div>
            </>
          )}

          {/* Step 2: Validating */}
          {step === 'validating' && (
            <div className="flex items-center justify-center py-8">
              <Loader className="w-8 h-8 animate-spin text-indigo-600 mr-3" />
              <span>Validiere Token...</span>
            </div>
          )}

          {/* Step 3: Confirm & OAuth2 URL */}
          {step === 'confirm' && validationResult && (
            <>
              <div className="bg-green-50 border border-green-200 rounded-md p-4">
                <div className="flex items-center">
                  <CheckCircle className="w-5 h-5 text-green-600 mr-2" />
                  <span className="text-sm font-medium text-green-800">Token validiert ✓</span>
                </div>
                <p className="text-sm text-green-700 mt-2">
                  App: <strong>{validationResult.appName}</strong> ({validationResult.appId})
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Berechtigungen</h3>
                <ul className="text-sm text-gray-700 space-y-1 list-disc list-inside">
                  {validationResult.permissionsHuman?.map((perm, i) => (
                    <li key={i}>{perm}</li>
                  ))}
                </ul>
              </div>

              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Discord Autorisierung</h3>
                <p className="text-sm text-gray-600 mb-3">
                  Klicke den Button um den Bot zu Discord hinzuzufügen:
                </p>
                <a
                  href={validationResult.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 transition font-medium"
                >
                  Öffne Discord Autorisierung
                  <ExternalLink className="w-4 h-4 ml-2" />
                </a>
              </div>

              <div className="bg-blue-50 border border-blue-200 rounded-md p-3">
                <p className="text-xs text-blue-800">
                  💡 Alternativ kannst du diese URL kopieren und selbst öffnen:
                </p>
                <div className="mt-2 flex items-center justify-between bg-white border border-blue-100 rounded p-2">
                  <code className="text-xs font-mono text-gray-700 overflow-hidden text-ellipsis">
                    {validationResult.url?.substring(0, 50)}...
                  </code>
                  <button
                    onClick={() => validationResult.url && copyToClipboard(validationResult.url)}
                    className="ml-2 p-1 hover:bg-gray-100 rounded transition"
                    title="Copy URL"
                  >
                    {copied ? (
                      <CheckCircle className="w-4 h-4 text-emerald-600" />
                    ) : (
                      <Copy className="w-4 h-4 text-gray-500" />
                    )}
                  </button>
                </div>
              </div>

              <div className="border-t pt-4">
                <p className="text-sm text-gray-600 mb-3">
                  Nach der Autorisierung speichern wir den Token lokal:
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
                Dein Discord-Bot ist jetzt bereit. Der Daemon startet neu und verbindet sich mit Discord.
              </p>
              <p className="text-xs text-gray-500 mb-4">
                Falls der Bot nicht sofort antwortet, kann es 30 Sekunden dauern bis die Verbindung hergestellt ist.
              </p>
              <button
                onClick={() => window.location.reload()}
                className="px-6 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 transition font-medium"
              >
                Schließen &amp; Neu laden
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
              href="https://discord.com/developers/applications"
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-600 hover:underline"
            >
              Discord Developer Portal öffnen
            </a>
            {' → '}
            Applications {' → '} New Application {' → '} Bot {' → '} Copy Token
          </p>
        </div>
      </div>
    </div>
  )
}
