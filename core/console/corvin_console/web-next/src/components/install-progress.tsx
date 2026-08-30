/**
 * Install Progress Modal - Phase 2 Task #6
 * 5-step installer: Validate → Download → Unpack → Register → Test
 * Mocked polling; Phase 3 will wire real job API
 */

import React, { useReducer, useEffect, useRef, useCallback } from 'react'
import { X, AlertCircle, CheckCircle, Loader } from 'lucide-react'

export interface InstallProgressProps {
  extensionId: string
  extensionName: string
  onClose: () => void
  onComplete?: () => void
}

interface ProgressState {
  step: 0 | 1 | 2 | 3 | 4 | 5 // 0=start, 1-5=steps, enum by step_id
  stepNames: ['Validate', 'Download', 'Unpack', 'Register', 'Test']
  progress: number // 0-100
  eta: number // seconds remaining
  error: string | null
  cancelled: boolean
  completed: boolean
}

type ProgressAction =
  | { type: 'STEP_START'; step: number }
  | { type: 'PROGRESS_UPDATE'; progress: number; eta: number }
  | { type: 'STEP_COMPLETE'; step: number }
  | { type: 'ERROR'; message: string }
  | { type: 'CANCEL' }
  | { type: 'COMPLETE' }

const initialState: ProgressState = {
  step: 0,
  stepNames: ['Validate', 'Download', 'Unpack', 'Register', 'Test'],
  progress: 0,
  eta: 10,
  error: null,
  cancelled: false,
  completed: false,
}

function progressReducer(state: ProgressState, action: ProgressAction): ProgressState {
  switch (action.type) {
    case 'STEP_START':
      return { ...state, step: action.step as (0|1|2|3|4|5), error: null }
    case 'PROGRESS_UPDATE':
      return { ...state, progress: action.progress, eta: action.eta }
    case 'STEP_COMPLETE':
      const nextStep = (action.step + 1) as (0|1|2|3|4|5)
      return { ...state, step: nextStep, progress: 0, eta: 0 }
    case 'ERROR':
      return { ...state, error: action.message, cancelled: true }
    case 'CANCEL':
      return { ...state, cancelled: true }
    case 'COMPLETE':
      return { ...state, completed: true, progress: 100, step: 5, eta: 0 }
    default:
      return state
  }
}

// Mock job: 100 steps over 10 seconds, one step per 100ms
// Step progression: 0→1 @20%, 1→2 @40%, 2→3 @60%, 3→4 @80%, 4→5 @100%
async function runMockInstallJob(
  _extensionId: string, // unused in mock; Phase 3 will use for API
  onProgress: (progress: number, step: number, eta: number) => void,
  onComplete: () => void,
  onError: (msg: string) => void,
  onCancelled: () => boolean // return true if cancel flag set
): Promise<void> {
  const totalSteps = 100
  const stepInterval = 100 // ms
  const stepDuration = (totalSteps * stepInterval) / 1000 // seconds

  for (let i = 0; i <= totalSteps; i++) {
    if (onCancelled()) {
      onError('Installation cancelled')
      return
    }

    const progress = (i / totalSteps) * 100
    const eta = Math.max(0, stepDuration - (i / totalSteps) * stepDuration)

    // Determine which step we're in (1-5)
    let currentStep = Math.max(1, Math.min(5, Math.ceil((progress / 100) * 5))) as (1|2|3|4|5)

    onProgress(progress, currentStep, eta)

    await new Promise(resolve => setTimeout(resolve, stepInterval))
  }

  onComplete()
}

export const InstallProgress: React.FC<InstallProgressProps> = ({
  extensionId,
  extensionName,
  onClose,
  onComplete,
}) => {
  const [state, dispatch] = useReducer(progressReducer, initialState)
  const jobRef = useRef<Promise<void> | null>(null)
  const cancelledRef = useRef(false)

  const currentStepName = state.step > 0 && state.step <= 5
    ? state.stepNames[state.step - 1]
    : 'Starting...'

  // Start the mock job on mount
  useEffect(() => {
    if (!jobRef.current) {
      dispatch({ type: 'STEP_START', step: 1 })

      jobRef.current = runMockInstallJob(
        extensionId,
        (progress, step, eta) => {
          dispatch({ type: 'PROGRESS_UPDATE', progress, eta })
          dispatch({ type: 'STEP_START', step })
        },
        () => {
          dispatch({ type: 'COMPLETE' })
          if (onComplete) onComplete()
        },
        (message) => {
          dispatch({ type: 'ERROR', message })
        },
        () => cancelledRef.current
      )
    }
  }, [extensionId, onComplete])

  const handleCancel = useCallback(() => {
    cancelledRef.current = true
    dispatch({ type: 'CANCEL' })
    onClose()
  }, [onClose])

  const handleClose = useCallback(() => {
    if (state.completed || state.error) {
      onClose()
    }
  }, [state.completed, state.error, onClose])

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" data-testid="install-progress-backdrop">
      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-xl p-6 w-full max-w-md" data-testid="install-progress-modal" role="dialog">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Installing {extensionName}
          </h2>
          <button
            onClick={handleClose}
            className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            disabled={!state.completed && !state.error}
            data-testid="install-progress-close-btn"
          >
            <X size={20} />
          </button>
        </div>

        {/* Current Step */}
        <div className="mb-4">
          <div className="flex items-center gap-2 mb-2">
            {!state.error && !state.completed && <Loader size={16} className="animate-spin" />}
            {state.error && <AlertCircle size={16} className="text-red-500" />}
            {state.completed && <CheckCircle size={16} className="text-green-500" />}
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
              {currentStepName}
            </span>
          </div>

          {/* Step Indicators */}
          <div className="flex gap-1 mb-4">
            {state.stepNames.map((_, idx) => (
              <div
                key={idx}
                className={`flex-1 h-1 rounded-full ${
                  idx + 1 <= state.step
                    ? 'bg-blue-500'
                    : 'bg-gray-200 dark:bg-gray-700'
                }`}
              />
            ))}
          </div>
        </div>

        {/* Progress Bar */}
        <div className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-gray-600 dark:text-gray-400" data-testid="progress-percentage">
              {Math.round(state.progress)}%
            </span>
            <span className="text-xs text-gray-600 dark:text-gray-400">
              {state.eta > 0 ? `${Math.ceil(state.eta)}s remaining` : 'Complete'}
            </span>
          </div>
          <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2 overflow-hidden" data-testid="progress-bar">
            <div
              className="bg-blue-500 h-full rounded-full transition-all duration-100"
              style={{ width: `${state.progress}%` }}
            />
          </div>
        </div>

        {/* Error Message */}
        {state.error && (
          <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-md">
            <p className="text-sm text-red-700 dark:text-red-400">{state.error}</p>
          </div>
        )}

        {/* Status Message */}
        {!state.error && !state.completed && (
          <p className="text-xs text-gray-600 dark:text-gray-400 mb-4">
            Step {state.step} of 5: {currentStepName}
          </p>
        )}

        {state.completed && (
          <p className="text-xs text-green-600 dark:text-green-400 mb-4">
            Installation completed successfully
          </p>
        )}

        {/* Buttons */}
        <div className="flex gap-3 justify-end">
          {!state.completed && !state.error && (
            <button
              onClick={handleCancel}
              className="px-4 py-2 text-sm text-gray-700 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-800"
              data-testid="install-progress-cancel-btn"
            >
              Cancel
            </button>
          )}
          {(state.completed || state.error) && (
            <button
              onClick={handleClose}
              className="px-4 py-2 text-sm bg-blue-500 text-white rounded-md hover:bg-blue-600"
            >
              {state.completed ? 'Close' : 'Dismiss'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
