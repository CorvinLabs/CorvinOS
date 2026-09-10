/**
 * ToastNotification — Marketplace feedback (success/error)
 *
 * ADR-0297 Compliant: No PII in messages (no emails, paths, internal IDs)
 * Phases 3+: Success when install queued, errors on API failures
 */

import React, { useEffect } from 'react'
import { AlertCircle, Check, X } from 'lucide-react'
import { cn } from '@/lib/utils'

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
    success: 'bg-emerald-500/10 border-emerald-500/30',
    error: 'bg-destructive/10 border-destructive/40',
    info: 'bg-accent/10 border-accent/30',
  }[toast.type]

  const textColor = {
    success: 'text-emerald-700 dark:text-emerald-400',
    error: 'text-destructive',
    info: 'text-accent-foreground',
  }[toast.type]

  const Icon = {
    success: Check,
    error: AlertCircle,
    info: AlertCircle,
  }[toast.type]

  return (
    <div
      className={cn('rounded-lg border p-4 flex items-start gap-3', bgColor)}
      role="alert"
      data-testid={`toast-${toast.type}`}
    >
      <Icon className={cn('w-5 h-5 flex-shrink-0', textColor)} />
      <p className={cn('text-sm', textColor)}>{toast.message}</p>
      <button
        onClick={onDismiss}
        className={cn('flex-shrink-0 hover:opacity-75', textColor)}
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}

export default ToastNotification
