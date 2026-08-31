import { useEffect, useState, useRef } from 'react';

export interface TaskEvent {
  seq: number;
  timestamp: number;
  event: string;
  [key: string]: unknown;
}

/**
 * Hook for accessing cached task events from IndexedDB.
 * Useful for offline mode: when reconnecting, user sees cached events immediately.
 *
 * ADR-0080 M3: Offline event caching via IndexedDB.
 */
export function useTaskIDB(taskId: string | null) {
  const [cachedEvents, setCachedEvents] = useState<TaskEvent[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!taskId) return;

    const fetchCached = async () => {
      if (!('indexedDB' in window)) return;

      setLoading(true);
      try {
        const db = await openDB();
        const tx = db.transaction('task_events', 'readonly');
        const store = tx.objectStore('task_events');
        const index = store.index('task_id');
        const request = index.getAll(taskId);

        request.onsuccess = () => {
          // Sort by seq ascending
          const events = (request.result as TaskEvent[]).sort((a, b) => a.seq - b.seq);
          setCachedEvents(events);
        };

        request.onerror = () => {
          console.error('Failed to read cached events:', request.error);
        };
      } catch (err) {
        console.error('Failed to access IndexedDB:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchCached();
  }, [taskId]);

  return { cachedEvents, loading };
}

export interface UseTaskIDBSyncOptions {
  taskId: string | null;
  onEvent?: (event: TaskEvent) => void;
}

/**
 * Enhanced hook: read + write (sync on events).
 * ADR-0082 M2: Write-on-event batching, debounce, 10 events/sec max.
 */
export function useTaskIDBSync({
  taskId,
  onEvent,
}: UseTaskIDBSyncOptions) {
  const [cachedEvents, setCachedEvents] = useState<TaskEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const pendingWritesRef = useRef<TaskEvent[]>([]);
  const flushTimerRef = useRef<NodeJS.Timeout | null>(null);
  const lastWriteTimeRef = useRef<number>(0);

  // Initial read
  useEffect(() => {
    if (!taskId) return;

    const fetchCached = async () => {
      if (!('indexedDB' in window)) return;

      setLoading(true);
      try {
        const db = await openDB();
        const tx = db.transaction('task_events', 'readonly');
        const store = tx.objectStore('task_events');
        const index = store.index('task_id');
        const request = index.getAll(taskId);

        request.onsuccess = () => {
          const events = (request.result as TaskEvent[]).sort((a, b) => a.seq - b.seq);
          setCachedEvents(events);
        };

        request.onerror = () => {
          console.error('Failed to read cached events:', request.error);
        };
      } catch (err) {
        console.error('Failed to access IndexedDB:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchCached();
  }, [taskId]);

  // Write-on-event batching
  const writeEvent = (event: TaskEvent) => {
    if (!taskId || !('indexedDB' in window)) {
      onEvent?.(event);
      return;
    }

    // Throttle: 10 events/sec max
    const now = Date.now();
    if (now - lastWriteTimeRef.current < 100) {
      pendingWritesRef.current.push(event);
      onEvent?.(event);
      return;
    }

    // Flush immediately
    pendingWritesRef.current.push(event);
    flushIDB(taskId, pendingWritesRef.current);
    lastWriteTimeRef.current = now;
    pendingWritesRef.current = [];
    onEvent?.(event);

    // Debounce future flushes (500ms)
    if (flushTimerRef.current) clearTimeout(flushTimerRef.current);
    flushTimerRef.current = setTimeout(() => {
      if (pendingWritesRef.current.length > 0) {
        flushIDB(taskId, pendingWritesRef.current);
        pendingWritesRef.current = [];
      }
    }, 500);
  };

  useEffect(() => {
    return () => {
      if (flushTimerRef.current) clearTimeout(flushTimerRef.current);
    };
  }, []);

  return { cachedEvents, loading, writeEvent };
}

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('corvin_tasks', 1);

    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);

    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result;
      if (!db.objectStoreNames.contains('task_events')) {
        const store = db.createObjectStore('task_events', { keyPath: 'id', autoIncrement: true });
        store.createIndex('task_id', 'task_id', { unique: false });
        store.createIndex('timestamp', 'timestamp', { unique: false });
      }
    };
  });
}

async function flushIDB(taskId: string, events: TaskEvent[]): Promise<void> {
  try {
    const db = await openDB();
    const tx = db.transaction('task_events', 'readwrite');
    const store = tx.objectStore('task_events');

    for (const event of events) {
      store.put({ task_id: taskId, ...event });
    }
  } catch (err) {
    console.error('Failed to flush events to IDB:', err);
  }
}
