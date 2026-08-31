/**
 * Unified IndexedDB service for task persistence.
 * ADR-0082 M2: Frontend Persistence Layer
 *
 * Schema: database "corvin_tasks" v2
 * - ObjectStore "tasks": keyPath="task_id"
 *   - task_id: string (UUID)
 *   - chat_key: string
 *   - persona: string
 *   - instruction: string
 *   - status: "pending" | "running" | "completed" | "failed"
 *   - created_at: number (timestamp ms)
 *   - started_at: number | null
 *   - completed_at: number | null
 *   - progress_pct: 0-100
 *   - latest_line: string (last output)
 *   - result: string (full output)
 *   - error: string | null
 *   - last_synced_at: number
 *   - synced: boolean
 *   - etag: string | null (for polling fallback)
 *
 * Indexes:
 *   - chat_key
 *   - status
 *   - created_at
 *   - last_synced_at
 */

export interface Task {
  task_id: string;
  chat_key: string;
  persona: string;
  instruction: string;
  status: "pending" | "running" | "completed" | "failed";
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
  progress_pct: number;
  latest_line: string;
  result: string;
  error: string | null;
  last_synced_at: number;
  synced: boolean;
  etag: string | null;
}

const DB_NAME = "corvin_tasks";
const DB_VERSION = 2;
const STORE_NAME = "tasks";

let dbInstance: IDBDatabase | null = null;

export async function openTaskDB(): Promise<IDBDatabase> {
  if (dbInstance) return dbInstance;

  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      dbInstance = request.result;
      resolve(dbInstance);
    };

    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result;

      // Remove old store if exists (migration from v1)
      if (db.objectStoreNames.contains("task_events")) {
        db.deleteObjectStore("task_events");
      }

      // Create/upgrade tasks store
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: "task_id" });
        store.createIndex("chat_key", "chat_key", { unique: false });
        store.createIndex("status", "status", { unique: false });
        store.createIndex("created_at", "created_at", { unique: false });
        store.createIndex("last_synced_at", "last_synced_at", { unique: false });
      }
    };
  });
}

export async function saveTask(task: Task): Promise<void> {
  if (!("indexedDB" in window)) return;

  try {
    const db = await openTaskDB();
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);

    return new Promise((resolve, reject) => {
      const request = store.put(task);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  } catch (err) {
    console.error("Failed to save task to IndexedDB:", err);
  }
}

export async function getTask(taskId: string): Promise<Task | null> {
  if (!("indexedDB" in window)) return null;

  try {
    const db = await openTaskDB();
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);

    return new Promise((resolve, reject) => {
      const request = store.get(taskId);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result || null);
    });
  } catch (err) {
    console.error("Failed to get task from IndexedDB:", err);
    return null;
  }
}

export async function getTasksByChatKey(chatKey: string): Promise<Task[]> {
  if (!("indexedDB" in window)) return [];

  try {
    const db = await openTaskDB();
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const index = store.index("chat_key");

    return new Promise((resolve, reject) => {
      const request = index.getAll(chatKey);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        const tasks = (request.result as Task[]).sort(
          (a, b) => b.created_at - a.created_at
        );
        resolve(tasks);
      };
    });
  } catch (err) {
    console.error("Failed to get tasks by chat_key from IndexedDB:", err);
    return [];
  }
}

export async function getRunningTasks(): Promise<Task[]> {
  if (!("indexedDB" in window)) return [];

  try {
    const db = await openTaskDB();
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const index = store.index("status");

    return new Promise((resolve, reject) => {
      const request = index.getAll("running");
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        const tasks = (request.result as Task[]).sort(
          (a, b) => b.created_at - a.created_at
        );
        resolve(tasks);
      };
    });
  } catch (err) {
    console.error("Failed to get running tasks from IndexedDB:", err);
    return [];
  }
}

export async function deleteTask(taskId: string): Promise<void> {
  if (!("indexedDB" in window)) return;

  try {
    const db = await openTaskDB();
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);

    return new Promise((resolve, reject) => {
      const request = store.delete(taskId);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  } catch (err) {
    console.error("Failed to delete task from IndexedDB:", err);
  }
}

export async function deleteTasksByChatKey(chatKey: string): Promise<void> {
  if (!("indexedDB" in window)) return;

  try {
    const tasks = await getTasksByChatKey(chatKey);
    for (const task of tasks) {
      await deleteTask(task.task_id);
    }
  } catch (err) {
    console.error("Failed to delete tasks by chat_key:", err);
  }
}

export async function cleanupOldTasks(olderThanMs: number): Promise<number> {
  if (!("indexedDB" in window)) return 0;

  try {
    const db = await openTaskDB();
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    const index = store.index("created_at");
    const cutoff = Date.now() - olderThanMs;

    let deletedCount = 0;
    return new Promise((resolve, reject) => {
      const request = index.openCursor();
      request.onerror = () => reject(request.error);
      request.onsuccess = (event) => {
        const cursor = (event.target as IDBRequest).result;
        if (cursor) {
          const task = cursor.value as Task;
          if (task.created_at < cutoff) {
            cursor.delete();
            deletedCount++;
          }
          cursor.continue();
        } else {
          resolve(deletedCount);
        }
      };
    });
  } catch (err) {
    console.error("Failed to cleanup old tasks:", err);
    return 0;
  }
}

export async function getAllTasks(): Promise<Task[]> {
  if (!("indexedDB" in window)) return [];

  try {
    const db = await openTaskDB();
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);

    return new Promise((resolve, reject) => {
      const request = store.getAll();
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        const tasks = (request.result as Task[]).sort(
          (a, b) => b.created_at - a.created_at
        );
        resolve(tasks);
      };
    });
  } catch (err) {
    console.error("Failed to get all tasks from IndexedDB:", err);
    return [];
  }
}

export async function exportTaskAsJSON(taskId: string): Promise<string | null> {
  try {
    const task = await getTask(taskId);
    if (!task) return null;
    return JSON.stringify(task, null, 2);
  } catch (err) {
    console.error("Failed to export task as JSON:", err);
    return null;
  }
}

export async function importTaskFromJSON(jsonString: string): Promise<boolean> {
  try {
    const task = JSON.parse(jsonString) as Task;
    // Validate schema
    if (!task.task_id || !task.chat_key) {
      throw new Error("Invalid task schema: missing task_id or chat_key");
    }
    await saveTask(task);
    return true;
  } catch (err) {
    console.error("Failed to import task from JSON:", err);
    return false;
  }
}
