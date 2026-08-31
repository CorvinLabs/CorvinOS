import { useEffect, useRef, useCallback, useState } from 'react';

export interface TaskEvent {
  seq: number;
  timestamp: number;
  event: string;
  [key: string]: unknown;
}

export interface UseTaskSSEOptions {
  taskId: string | null;
  sessionId: string | null;
  onEvent?: (event: TaskEvent) => void;
  onError?: (error: Error) => void;
  autoReconnect?: boolean;
  reconnectDelay?: number;
}

/**
 * React hook for streaming task events via SSE (Server-Sent Events).
 * Automatically handles reconnection with Last-Event-ID.
 * Coordinates with Service Worker for offline support (M3+).
 *
 * ADR-0080 M2+: Task event streaming with browser reconnect + offline caching.
 */
export function useTaskSSE({
  taskId,
  sessionId,
  onEvent,
  onError,
  autoReconnect = true,
  reconnectDelay = 3000,
}: UseTaskSSEOptions) {
  const eventSourceRef = useRef<EventSource | null>(null);
  const lastEventIdRef = useRef<number | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const swRef = useRef<ServiceWorkerContainer | null>(null);

  // Use refs for callbacks so that changing the callback never triggers a
  // reconnect. The SSE connection should only reconnect when taskId/sessionId
  // actually change, not on every parent re-render that creates new closures.
  const onEventRef = useRef(onEvent);
  const onErrorRef = useRef(onError);
  useEffect(() => { onEventRef.current = onEvent; });
  useEffect(() => { onErrorRef.current = onError; });

  const connect = useCallback(() => {
    if (!taskId || !sessionId) return;

    // Close existing connection
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    const url = new URL(
      `/v1/console/chat/sessions/${sessionId}/tasks/${taskId}/events`,
      window.location.origin
    );

    // Add Last-Event-ID query param if we have it
    if (lastEventIdRef.current !== null) {
      url.searchParams.append('last_event_id', String(lastEventIdRef.current));
    }

    const eventSource = new EventSource(url.toString());

    eventSource.onopen = () => {
      setIsConnected(true);
    };

    eventSource.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as TaskEvent;

        // Update last received seq for reconnect
        if (typeof data.seq === 'number') {
          lastEventIdRef.current = data.seq;
        }

        // Update events list
        setEvents((prev) => [...prev, data]);

        // Call user callback via ref — avoids closing/reopening on every render
        onEventRef.current?.(data);

        // Auto-close on task completion
        if (data.event === 'task.completed' || data.event === 'task.failed') {
          eventSource.close();
          setIsConnected(false);
        }
      } catch (err) {
        onErrorRef.current?.(
          new Error(`Failed to parse task event: ${err instanceof Error ? err.message : String(err)}`)
        );
      }
    };

    eventSource.onerror = () => {
      eventSource.close();
      setIsConnected(false);

      if (autoReconnect) {
        reconnectTimeoutRef.current = setTimeout(connect, reconnectDelay);
      }

      onErrorRef.current?.(new Error('SSE connection failed'));
    };

    eventSourceRef.current = eventSource;
  // Intentionally exclude onEvent/onError — use refs above so that new
  // callback references from the parent never cause a reconnect.
  }, [taskId, sessionId, autoReconnect, reconnectDelay]);

  // Register Service Worker and start background streaming (M3)
  useEffect(() => {
    if (!taskId || !sessionId) return;

    const registerSW = async () => {
      if (!('serviceWorker' in navigator)) return;

      try {
        const registration = await navigator.serviceWorker.register('/sw.js');
        swRef.current = navigator.serviceWorker;

        // Tell Service Worker to start streaming
        if (registration.active) {
          registration.active.postMessage({
            type: 'START_SSE',
            taskId,
            sessionId,
          });
        }

        // Listen for messages from Service Worker
        navigator.serviceWorker.onmessage = (event) => {
          if (event.data.type === 'TASK_EVENT' && event.data.taskId === taskId) {
            const taskEvent = event.data.event as TaskEvent;
            setEvents((prev) => [...prev, taskEvent]);
            onEvent?.(taskEvent);

            // Update last received seq
            if (typeof taskEvent.seq === 'number') {
              lastEventIdRef.current = taskEvent.seq;
            }
          }
        };
      } catch (err) {
        console.error('Failed to register Service Worker:', err);
      }
    };

    registerSW();
  }, [taskId, sessionId, onEvent]);

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
      // Tell Service Worker to stop streaming
      if (swRef.current?.controller) {
        swRef.current.controller.postMessage({
          type: 'STOP_SSE',
          taskId,
        });
      }
    };
  }, [connect]);

  return {
    isConnected,
    events,
    lastEventSeq: lastEventIdRef.current,
  };
}
