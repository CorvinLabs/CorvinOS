/**
 * ToastNotification — Marketplace feedback (success/error)
 *
 * ADR-0297 Compliant: No PII in messages (no emails, paths, internal IDs)
 * Phases 3+: Success when install queued, errors on API failures
 */

import React, { useEffect } from 'react'
import { AlertCircle, Check, X } from 'lucide-react'

export type ToastType = 'success' | 'error' | 'info'

export interface ToastMessage {
  id: string
  type: ToastType
  message: string
  duration?: number // ms, 0 = persist
}

interface ToastNotificationProps {
  toasts: ToastMessage[]
  onDismiss: (id: string) => void
}

export const ToastNotification: React.FC<ToastNotificationProps> = ({ toasts, onDismiss }) => {
  return (
    <div className="fixed bottom-4 right-4 space-y-3 z-50 max-w-sm">
      {toasts.map((toast) => (
        <Toast
          key={toast.id}
          toast={toast}
          onDismiss={() => onDismiss(toast.id)}
        />
      ))}
    </div>
  )
}

interface ToastProps {
  toast: ToastMessage
  onDismiss: () => void
}

const Toast: React.FC<ToastProps> = ({ toast, onDismiss }) => {
  useEffect(() => {
    if (toast.duration && toast.duration > 0) {
      const timer = setTimeout(onDismiss, toast.duration)
      return () => clearTimeout(timer)
    }
  }, [toast.duration, onDismiss])

  const bgColor = {
    success: 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-700',
    error: 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-700',
    info: 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-700',
  }[toast.type]

  const textColor = {
    success: 'text-green-800 dark:text-green-200',
    error: 'text-red-800 dark:text-red-200',
    info: 'text-blue-800 dark:text-blue-200',
  }[toast.type]

  const Icon = {
    success: Check,
    error: AlertCircle,
    info: AlertCircle,
  }[toast.type]

  return (
    <div
      className={`rounded-lg border p-4 flex items-start gap-3 ${bgColor}`}
      role="alert"
      data-testid={`toast-${toast.type}`}
    >
      <Icon className={`w-5 h-5 flex-shrink-0 ${textColor}`} />
      <p className={`text-sm ${textColor}`}>{toast.message}</p>
      <button
        onClick={onDismiss}
        className={`flex-shrink-0 ${textColor} hover:opacity-75`}
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}

export default ToastNotification
