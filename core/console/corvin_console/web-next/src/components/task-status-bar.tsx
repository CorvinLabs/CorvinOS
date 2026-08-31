import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
// M3+: useTaskSSE and useTaskIDB will be used when integrating with real SSE streams
// import { useTaskSSE } from '@/hooks/use-task-sse';
// import { useTaskIDB } from '@/hooks/use-task-idb';

interface RunningTask {
  task_id: string;
  session_id: string;
  instruction: string;
  status: string;
  isOfflineCached?: boolean;
}

/**
 * Helper to open IndexedDB for offline task cache.
 * ADR-0080 M3: Offline cache fallback when API is unreachable.
 */
function openIndexedDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('corvin_tasks', 1);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);
    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result;
      if (!db.objectStoreNames.contains('task_events')) {
        const store = db.createObjectStore('task_events', { keyPath: 'id', autoIncrement: true });
        store.createIndex('task_id', 'task_id', { unique: false });
      }
    };
  });
}

/**
 * Global task status bar — persistent top-level tracker.
 * Shows running tasks across all sessions.
 * Falls back to offline cache (M3) when API is unreachable.
 *
 * ADR-0080 M2+M3: UI component for task status visibility + offline support.
 */
export function TaskStatusBar() {
  const [runningTasks, setRunningTasks] = useState<RunningTask[]>([]);
  const [isExpanded, setIsExpanded] = useState(false);

  // Fetch running tasks + fallback to offline cache (M3)
  useEffect(() => {
    const fetchRunningTasks = async () => {
      try {
        const response = await fetch('/v1/console/chat/sessions', { credentials: 'include' });
        if (!response.ok) throw new Error('Sessions API failed');

        const data = (await response.json()) as { sessions: Array<{ sid: string }> };

        // For each session, fetch its running tasks
        const allTasks: RunningTask[] = [];
        for (const session of data.sessions) {
          const tasksResponse = await fetch(
            `/v1/console/chat/sessions/${session.sid}/tasks?status=running&limit=10`,
            { credentials: 'include' }
          );
          if (tasksResponse.ok) {
            const tasksData = (await tasksResponse.json()) as {
              tasks: Array<{ task_id: string; status: string; input: { instruction: string } }>;
            };
            allTasks.push(
              ...tasksData.tasks.map((t) => ({
                task_id: t.task_id,
                session_id: session.sid,
                instruction: (t.input as { instruction: string }).instruction,
                status: t.status,
              }))
            );
          }
        }
        setRunningTasks(allTasks);
      } catch (err) {
        console.error('Failed to fetch running tasks (will try offline cache):', err);
        // M3: On network error, check IndexedDB for cached tasks
        if ('indexedDB' in window) {
          try {
            const db = await openIndexedDB();
            const tx = db.transaction('task_events', 'readonly');
            const store = tx.objectStore('task_events');
            const allEvents = await new Promise<Array<{ task_id: string; [key: string]: unknown }>>(
              (resolve, reject) => {
                const request = store.getAll();
                request.onsuccess = () => resolve(request.result as Array<{ task_id: string; [key: string]: unknown }>);
                request.onerror = () => reject(request.error);
              }
            );

            // Group by task_id and find running ones
            const taskMap = new Map<string, RunningTask>();
            for (const evt of allEvents) {
              if (!taskMap.has(evt.task_id as string)) {
                taskMap.set(evt.task_id as string, {
                  task_id: evt.task_id as string,
                  session_id: '', // offline, don't know session
                  instruction: '', // offline, don't know instruction
                  status: 'running',
                  isOfflineCached: true,
                });
              }
            }
            setRunningTasks(Array.from(taskMap.values()));
          } catch (idbErr) {
            console.error('Offline cache lookup also failed:', idbErr);
          }
        }
      }
    };

    fetchRunningTasks();
    const interval = setInterval(fetchRunningTasks, 5000);
    return () => clearInterval(interval);
  }, []);

  if (runningTasks.length === 0) {
    return null;
  }

  return (
    <div className="border-b bg-blue-50 px-4 py-2">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-2 text-sm font-medium text-blue-900"
      >
        <span className="inline-block h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
        {runningTasks.length} task{runningTasks.length !== 1 ? 's' : ''} running
        <span>{isExpanded ? '−' : '+'}</span>
      </button>

      {isExpanded && (
        <div className="mt-2 space-y-1">
          {runningTasks.map((task) => (
            <div
              key={task.task_id}
              className={`flex items-center justify-between rounded px-3 py-2 text-xs ${
                task.isOfflineCached ? 'bg-gray-100' : 'bg-white'
              }`}
            >
              <div>
                <div className="flex items-center gap-2">
                  <div className="font-mono text-gray-500">{task.task_id.slice(0, 8)}...</div>
                  {task.isOfflineCached && (
                    <span className="rounded bg-gray-300 px-1.5 py-0.5 text-xs font-semibold text-gray-700">
                      offline
                    </span>
                  )}
                </div>
                <div className="max-w-sm truncate text-gray-700">
                  {task.instruction.slice(0, 60)}
                  {task.instruction.length > 60 ? '…' : ''}
                </div>
              </div>
              {task.session_id && (
                <Link
                  to={`/app/chat/sessions/${task.session_id}`}
                  className="ml-2 whitespace-nowrap rounded bg-blue-500 px-2 py-1 text-white hover:bg-blue-600"
                >
                  Open
                </Link>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
