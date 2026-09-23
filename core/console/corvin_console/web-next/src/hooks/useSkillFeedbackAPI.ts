/**
 * useSkillFeedbackAPI — Hook for submitting feedback to Stream 4 endpoints (ADR-2050)
 * Handles:
 * - Form validation (PII detection, bounds)
 * - CSRF token injection
 * - Error handling (400 validation, 503 service unavailable)
 * - Loading states
 */

import { useState, useCallback } from 'react';
import {
  OutcomeFeedbackRequest,
  PreferenceFeedbackRequest,
  ConfidenceFeedbackRequest,
  MetricFeedbackRequest,
  FeedbackResponse,
  FeedbackErrorResponse,
} from '@/types/feedback';

export interface FeedbackSubmitOptions {
  onSuccess?: (response: FeedbackResponse) => void;
  onError?: (error: string) => void;
}

export interface UseSkillFeedbackAPIState {
  loading: boolean;
  error: string | null;
  success: boolean;
}

/**
 * Hook for submitting feedback to Stream 4 endpoints.
 * Automatically injects CSRF token from DOM.
 */
export function useSkillFeedbackAPI() {
  const [state, setState] = useState<UseSkillFeedbackAPIState>({
    loading: false,
    error: null,
    success: false,
  });

  /**
   * Extract CSRF token from DOM (meta tag)
   * Injected by Flask app at boot
   */
  const getCsrfToken = useCallback(() => {
    const token = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
    if (!token) {
      throw new Error('CSRF token not found in DOM');
    }
    return token;
  }, []);

  /**
   * Submit outcome feedback (Stream 1 & 2)
   */
  const submitOutcomeFeedback = useCallback(
    async (request: OutcomeFeedbackRequest, options?: FeedbackSubmitOptions) => {
      setState({ loading: true, error: null, success: false });
      try {
        const csrfToken = getCsrfToken();
        const endpoint =
          request.skill_id === 'os.security_orchestrator'
            ? '/v1/console/learning/security-orchestrator/incident'
            : '/v1/console/learning/workflow-optimizer/feedback';

        const response = await fetch(endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken,
          },
          credentials: 'include',
          body: JSON.stringify(request),
        });

        if (!response.ok) {
          const error = (await response.json()) as FeedbackErrorResponse;
          const errorMsg = error.detail || error.error || 'Failed to submit feedback';
          setState({ loading: false, error: errorMsg, success: false });
          options?.onError?.(errorMsg);
          return null;
        }

        const data = (await response.json()) as FeedbackResponse;
        setState({ loading: false, error: null, success: true });
        options?.onSuccess?.(data);
        return data;
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : 'Unknown error';
        setState({ loading: false, error: errorMsg, success: false });
        options?.onError?.(errorMsg);
        return null;
      }
    },
    [getCsrfToken]
  );

  /**
   * Submit preference feedback (Stream 3)
   */
  const submitPreferenceFeedback = useCallback(
    async (request: PreferenceFeedbackRequest, options?: FeedbackSubmitOptions) => {
      setState({ loading: true, error: null, success: false });
      try {
        const csrfToken = getCsrfToken();

        const response = await fetch('/v1/console/learning/flow-guard/policy-feedback', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken,
          },
          credentials: 'include',
          body: JSON.stringify(request),
        });

        if (!response.ok) {
          const error = (await response.json()) as FeedbackErrorResponse;
          const errorMsg = error.detail || error.error || 'Failed to submit feedback';
          setState({ loading: false, error: errorMsg, success: false });
          options?.onError?.(errorMsg);
          return null;
        }

        const data = (await response.json()) as FeedbackResponse;
        setState({ loading: false, error: null, success: true });
        options?.onSuccess?.(data);
        return data;
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : 'Unknown error';
        setState({ loading: false, error: errorMsg, success: false });
        options?.onError?.(errorMsg);
        return null;
      }
    },
    [getCsrfToken]
  );

  /**
   * Submit metrics observation
   */
  const submitMetricFeedback = useCallback(
    async (request: MetricFeedbackRequest, options?: FeedbackSubmitOptions) => {
      setState({ loading: true, error: null, success: false });
      try {
        const csrfToken = getCsrfToken();

        const response = await fetch('/v1/console/learning/metrics/observe', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken,
          },
          credentials: 'include',
          body: JSON.stringify(request),
        });

        if (!response.ok) {
          const error = (await response.json()) as FeedbackErrorResponse;
          const errorMsg = error.detail || error.error || 'Failed to submit metric';
          setState({ loading: false, error: errorMsg, success: false });
          options?.onError?.(errorMsg);
          return null;
        }

        const data = (await response.json()) as FeedbackResponse;
        setState({ loading: false, error: null, success: true });
        options?.onSuccess?.(data);
        return data;
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : 'Unknown error';
        setState({ loading: false, error: errorMsg, success: false });
        options?.onError?.(errorMsg);
        return null;
      }
    },
    [getCsrfToken]
  );

  /**
   * Reset state (for clearing success toast)
   */
  const reset = useCallback(() => {
    setState({ loading: false, error: null, success: false });
  }, []);

  return {
    ...state,
    submitOutcomeFeedback,
    submitPreferenceFeedback,
    submitMetricFeedback,
    reset,
  };
}
