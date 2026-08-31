/**
 * Task recovery on page reload.
 * Hydrates from IDB, verifies via HTTP, re-subscribes to pub/sub.
 *
 * ADR-0082 M2.0 Phase 3: Page-reload resilience.
 */

export interface RecoveredTask {
  task_id: string;
  chat_key: string;
  status: string;
  created_at: number;
  started_at?: number;
  ended_at?: number;
  exit_code?: number;
}

async function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('corvin_tasks', 1);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);
    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result;
      if (!db.objectStoreNames.contains('tasks')) {
        const store = db.createObjectStore('tasks', { keyPath: 'task_id' });
        store.createIndex('chat_key', 'chat_key', { unique: false });
        store.createIndex('status', 'status', { unique: false });
      }
    };
  });
}

export async function recoverTaskState(): Promise<{
  tasks: Map<string, RecoveredTask>;
  recovered: number;
  failed: number;
}> {
  const result = {
    tasks: new Map<string, RecoveredTask>(),
    recovered: 0,
    failed: 0,
  };

  try {
    if (!('indexedDB' in window)) return result;

    const db = await openDB();

    // Get all tasks from IDB
    const tx = db.transaction('tasks', 'readonly');
    const store = tx.objectStore('tasks');
    const idbTasks = await new Promise<RecoveredTask[]>((resolve, reject) => {
      const request = store.getAll();
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result as RecoveredTask[]);
    });

    // Verify each task via HTTP (authoritative source)
    const taskIds = idbTasks.map((t) => t.task_id);
    if (taskIds.length === 0) {
      return result;
    }

    // Batch fetch: GET /v1/console/tasks?ids=id1,id2,id3
    const query = new URLSearchParams({ ids: taskIds.join(',') });
    const response = await fetch(`/v1/console/tasks?${query}`, {
      method: 'GET',
      credentials: 'include',
      signal: AbortSignal.timeout(5000),
    });

    if (response.ok) {
      const data = (await response.json()) as {
        tasks: RecoveredTask[];
      };

      // Update IDB with latest state
      const updateTx = db.transaction('tasks', 'readwrite');
      const updateStore = updateTx.objectStore('tasks');

      for (const task of data.tasks) {
        result.tasks.set(task.task_id, task);
        result.recovered++;
        await new Promise<void>((resolve, reject) => {
          const req = updateStore.put(task);
          req.onerror = () => reject(req.error);
          req.onsuccess = () => resolve();
        });
      }
    } else {
      // HTTP fetch failed: fall back to IDB state (stale but local)
      for (const task of idbTasks) {
        result.tasks.set(task.task_id, task);
        result.recovered++;
      }
    }
  } catch (err) {
    console.error('Task recovery error:', err);
  }

  return result;
}

export async function saveTaskToIDB(task: RecoveredTask): Promise<void> {
  if (!('indexedDB' in window)) return;

  try {
    const db = await openDB();
    const tx = db.transaction('tasks', 'readwrite');
    const store = tx.objectStore('tasks');

    return new Promise((resolve, reject) => {
      const request = store.put(task);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  } catch (err) {
    console.error('Failed to save task to IDB:', err);
  }
}
