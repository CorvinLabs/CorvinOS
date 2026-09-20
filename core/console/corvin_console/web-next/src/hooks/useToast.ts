import { useState, useCallback } from 'react';

export interface Toast {
  id: string;
  type: 'success' | 'error' | 'info' | 'warning';
  title: string;
  description?: string;
  duration?: number; // in milliseconds, 0 = indefinite
}

let toastId = 0;

/**
 * useToast: Simple toast notification hook
 * Usage: const { toasts, showToast, removeToast } = useToast();
 */
export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const showToast = useCallback(
    (
      type: Toast['type'],
      title: string,
      description?: string,
      duration = 5000
    ) => {
      const id = String(++toastId);
      const toast: Toast = {
        id,
        type,
        title,
        description,
        duration,
      };

      setToasts((prev) => [...prev, toast]);

      if (duration > 0) {
        setTimeout(() => {
          removeToast(id);
        }, duration);
      }

      return id;
    },
    []
  );

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return { toasts, showToast, removeToast };
}
