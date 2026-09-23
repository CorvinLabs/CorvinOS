/**
 * useSkillAdminData — Hooks for fetching admin panel data (Stream 1–3)
 * Handles:
 * - GET requests to admin endpoints
 * - Response caching (stale-after-seconds)
 * - Error handling + retry
 * - Loading states
 */

import { useState, useEffect, useCallback } from 'react';
import {
  WorkflowOptimizerAdminData,
  SecurityOrchestratorAdminData,
  FlowGuardAdminData,
  UnifiedDashboardData,
  AdminPanelResponse,
} from '@/types/admin-panels';

interface UseAdminDataState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  lastUpdate: string | null;
}

/**
 * Hook: Fetch Stream 1 (Workflow Optimizer) admin data
 */
export function useWorkflowOptimizerAdmin() {
  const [state, setState] = useState<UseAdminDataState<WorkflowOptimizerAdminData>>({
    data: null,
    loading: true,
    error: null,
    lastUpdate: null,
  });

  const refetch = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const response = await fetch('/v1/console/learning/workflow-optimizer/admin', {
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }

      const result = (await response.json()) as AdminPanelResponse<WorkflowOptimizerAdminData>;
      setState({
        data: result.data,
        loading: false,
        error: null,
        lastUpdate: result.timestamp,
      });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      setState((prev) => ({ ...prev, loading: false, error: errorMsg }));
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { ...state, refetch };
}

/**
 * Hook: Fetch Stream 2 (Security Orchestrator) admin data
 */
export function useSecurityOrchestratorAdmin() {
  const [state, setState] = useState<UseAdminDataState<SecurityOrchestratorAdminData>>({
    data: null,
    loading: true,
    error: null,
    lastUpdate: null,
  });

  const refetch = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const response = await fetch('/v1/console/learning/security-orchestrator/admin', {
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }

      const result = (await response.json()) as AdminPanelResponse<SecurityOrchestratorAdminData>;
      setState({
        data: result.data,
        loading: false,
        error: null,
        lastUpdate: result.timestamp,
      });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      setState((prev) => ({ ...prev, loading: false, error: errorMsg }));
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { ...state, refetch };
}

/**
 * Hook: Fetch Stream 3 (Flow Guard) admin data
 */
export function useFlowGuardAdmin() {
  const [state, setState] = useState<UseAdminDataState<FlowGuardAdminData>>({
    data: null,
    loading: true,
    error: null,
    lastUpdate: null,
  });

  const refetch = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const response = await fetch('/v1/console/learning/flow-guard/admin', {
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }

      const result = (await response.json()) as AdminPanelResponse<FlowGuardAdminData>;
      setState({
        data: result.data,
        loading: false,
        error: null,
        lastUpdate: result.timestamp,
      });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      setState((prev) => ({ ...prev, loading: false, error: errorMsg }));
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { ...state, refetch };
}

/**
 * Hook: Fetch unified dashboard data
 */
export function useUnifiedLearningDashboard(dateRange?: { start: string; end: string }) {
  const [state, setState] = useState<UseAdminDataState<UnifiedDashboardData>>({
    data: null,
    loading: true,
    error: null,
    lastUpdate: null,
  });

  const refetch = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const params = new URLSearchParams();
      if (dateRange) {
        params.append('start', dateRange.start);
        params.append('end', dateRange.end);
      }

      const response = await fetch(
        `/v1/console/learning/dashboard?${params.toString()}`,
        { credentials: 'include' }
      );

      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }

      const result = (await response.json()) as AdminPanelResponse<UnifiedDashboardData>;
      setState({
        data: result.data,
        loading: false,
        error: null,
        lastUpdate: result.timestamp,
      });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      setState((prev) => ({ ...prev, loading: false, error: errorMsg }));
    }
  }, [dateRange]);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { ...state, refetch };
}

/**
 * Hook: Generic admin data fetcher with retry logic
 */
export function useAdminDataWithRetry<T>(endpoint: string, retries = 3) {
  const [state, setState] = useState<UseAdminDataState<T>>({
    data: null,
    loading: true,
    error: null,
    lastUpdate: null,
  });

  const refetch = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));

    let lastError: Error | null = null;
    for (let attempt = 0; attempt < retries; attempt++) {
      try {
        const response = await fetch(endpoint, { credentials: 'include' });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const result = (await response.json()) as AdminPanelResponse<T>;
        setState({
          data: result.data,
          loading: false,
          error: null,
          lastUpdate: result.timestamp,
        });
        return;
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
        if (attempt < retries - 1) {
          await new Promise((resolve) => setTimeout(resolve, 500 * Math.pow(2, attempt)));
        }
      }
    }

    const errorMsg = lastError?.message || 'Failed to fetch after retries';
    setState((prev) => ({ ...prev, loading: false, error: errorMsg }));
  }, [endpoint, retries]);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { ...state, refetch };
}
