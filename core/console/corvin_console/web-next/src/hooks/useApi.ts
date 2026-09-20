import { useState, useCallback } from 'react';

interface UseApiOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: Record<string, any>;
  headers?: Record<string, string>;
}

interface UseApiState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
}

/**
 * useApi: Simple fetch wrapper for API calls
 * Usage: const { data, loading, error, fetch: callApi } = useApi<T>();
 */
export function useApi<T = any>() {
  const [state, setState] = useState<UseApiState<T>>({
    data: null,
    loading: false,
    error: null,
  });

  const fetch = useCallback(
    async (url: string, options: UseApiOptions = {}) => {
      setState({ data: null, loading: true, error: null });

      try {
        const requestOptions: RequestInit = {
          method: options.method || 'GET',
          headers: {
            'Content-Type': 'application/json',
            ...options.headers,
          },
        };

        if (options.body) {
          requestOptions.body = JSON.stringify(options.body);
        }

        const response = await global.fetch(url, requestOptions);

        if (!response.ok) {
          throw new Error(
            `API error: ${response.status} ${response.statusText}`
          );
        }

        const data = await response.json();
        setState({ data, loading: false, error: null });
        return data;
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        setState({ data: null, loading: false, error });
        throw error;
      }
    },
    []
  );

  return { ...state, fetch };
}
