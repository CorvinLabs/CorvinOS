/**
 * Service Worker for task event streaming and offline support.
 *
 * ADR-0080 M3: Background task tracking, event caching, notifications.
 *
 * Lifecycle:
 * 1. install — register for push/sync
 * 2. activate — clean up old caches
 * 3. fetch — pass-through (for now)
 * 4. message — receive instructions from client
 * 5. open SSE stream, cache events in IndexedDB
 */

const DB_NAME = 'corvin_tasks';
const DB_VERSION = 1;
const STORE_NAME = 'task_events';

// Open IndexedDB
function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);

    request.onupgradeneeded = (event) => {
      const db = event.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: 'id', autoIncrement: true });
        store.createIndex('task_id', 'task_id', { unique: false });
        store.createIndex('timestamp', 'timestamp', { unique: false });
      }
    };
  });
}

// Cache event in IndexedDB
async function cacheEvent(taskId, event) {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readwrite');
  const store = tx.objectStore(STORE_NAME);
  await store.add({ task_id: taskId, ...event, timestamp: Date.now() });
}

// Get cached events for a task
async function getCachedEvents(taskId) {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readonly');
  const store = tx.objectStore(STORE_NAME);
  const index = store.index('task_id');
  const request = index.getAll(taskId);

  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

// Clear cached events for a task
async function _clearCachedEvents(taskId) {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readwrite');
  const store = tx.objectStore(STORE_NAME);
  const index = store.index('task_id');
  const request = index.getAll(taskId);

  return new Promise((resolve, reject) => {
    request.onsuccess = () => {
      for (const event of request.result) {
        store.delete(event.id);
      }
      resolve();
    };
    request.onerror = () => reject(request.error);
  });
}

// Service Worker install
self.addEventListener('install', () => {
  self.skipWaiting();
});

// Service Worker activate
self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Handle messages from client
self.addEventListener('message', (event) => {
  const { type, taskId, sessionId } = event.data;

  if (type === 'START_SSE') {
    startSSEStream(taskId, sessionId);
  } else if (type === 'STOP_SSE') {
    stopSSEStream(taskId);
  }
});

const streams = new Map(); // taskId -> EventSource

async function startSSEStream(taskId, sessionId) {
  if (streams.has(taskId)) return;

  const url = new URL(
    `/v1/console/chat/sessions/${sessionId}/tasks/${taskId}/events`,
    self.location.origin
  );

  // Try to get last cached seq
  try {
    const cached = await getCachedEvents(taskId);
    if (cached.length > 0) {
      const lastSeq = cached[cached.length - 1].seq;
      url.searchParams.append('last_event_id', String(lastSeq));
    }
  } catch (err) {
    console.error('Failed to get cached events:', err);
  }

  const eventSource = new EventSource(url.toString());

  eventSource.onmessage = async (event) => {
    try {
      const data = JSON.parse(event.data);

      // Cache event
      await cacheEvent(taskId, data);

      // Notify clients
      self.clients.matchAll().then((clients) => {
        clients.forEach((client) => {
          client.postMessage({ type: 'TASK_EVENT', taskId, event: data });
        });
      });

      // Handle completion
      if (data.event === 'task.completed' || data.event === 'task.failed') {
        showNotification(taskId, data);
        eventSource.close();
        streams.delete(taskId);
      }
    } catch (err) {
      console.error('Failed to process event:', err);
    }
  };

  eventSource.onerror = () => {
    eventSource.close();
    streams.delete(taskId);
  };

  streams.set(taskId, eventSource);
}

function stopSSEStream(taskId) {
  const stream = streams.get(taskId);
  if (stream) {
    stream.close();
    streams.delete(taskId);
  }
}

async function showNotification(taskId, event) {
  if (!self.registration.showNotification) return;

  const title = event.event === 'task.completed' ? '✓ Task Complete' : '✗ Task Failed';
  const options = {
    body: `${taskId.slice(0, 8)}... ${event.event === 'task.completed' ? 'finished' : 'failed'}`,
    icon: '/favicon.ico',
    tag: `task-${taskId}`,
    requireInteraction: false,
  };

  try {
    await self.registration.showNotification(title, options);
  } catch (err) {
    console.error('Failed to show notification:', err);
  }
}
