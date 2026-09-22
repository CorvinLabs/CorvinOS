/**
 * orchestration-live-feed.tsx — Console UI for real-time orchestration events
 *
 * Shows live feed of background task batch completions with:
 * - Real-time event stream (WebSocket)
 * - Voice summary player (auto-play)
 * - Task breakdown (success/failed)
 * - Batch history
 */

'use client';

import { useEffect, useRef, useState } from 'react';
import styles from './orchestration-live-feed.module.css';

interface OrchestrationEvent {
  type: string;
  event_type: string;  // SUCCESS | MIXED
  batch_id: string;
  task_count: number;
  success_count: number;
  text: string;
  voice_attachment_url?: string;
  timestamp: number;
}

export function OrchestrationLiveFeed() {
  const [events, setEvents] = useState<OrchestrationEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    const ws = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/orchestration/ws/events`);

    ws.onopen = () => {
      setConnected(true);
      console.log('✓ Orchestration WebSocket connected');
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as OrchestrationEvent;

        // Add to live feed
        setEvents((prev) => [message, ...prev].slice(0, 50)); // Keep last 50

        // Auto-play voice summary if enabled
        if (message.voice_attachment_url) {
          playVoiceSummary(message.voice_attachment_url);
        }
      } catch (e) {
        console.error('Failed to parse orchestration event:', e);
      }
    };

    ws.onerror = () => {
      setConnected(false);
      console.error('✗ Orchestration WebSocket error');
    };

    ws.onclose = () => {
      setConnected(false);
      console.log('✗ Orchestration WebSocket closed');
    };

    wsRef.current = ws;

    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, []);

  const playVoiceSummary = (url: string) => {
    if (!audioRef.current) {
      audioRef.current = new Audio();
    }
    audioRef.current.src = url;
    audioRef.current.play().catch((e) => console.warn('Auto-play failed:', e));
  };

  return (
    <div className={styles.container}>
      {/* Header */}
      <div className={styles.header}>
        <h1>🎯 Background Tasks Live Feed</h1>
        <div className={styles.status}>
          <span className={connected ? styles.connected : styles.disconnected}>
            {connected ? '🟢 Live' : '🔴 Offline'}
          </span>
        </div>
      </div>

      {/* Audio Player (hidden) */}
      <audio ref={audioRef} style={{ display: 'none' }} />

      {/* Live Events Feed */}
      <div className={styles.feedContainer}>
        {events.length === 0 ? (
          <div className={styles.emptyState}>
            <p>Waiting for background tasks to complete...</p>
          </div>
        ) : (
          <div className={styles.eventsList}>
            {events.map((event) => (
              <OrchestrationEventCard
                key={event.batch_id}
                event={event}
                onPlayVoice={() => event.voice_attachment_url && playVoiceSummary(event.voice_attachment_url)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * OrchestrationEventCard — Display single orchestration event
 */
function OrchestrationEventCard({
  event,
  onPlayVoice,
}: {
  event: OrchestrationEvent;
  onPlayVoice: () => void;
}) {
  const isSuccess = event.event_type === 'ORCHESTRATION_COMPLETE_SUCCESS';
  const failedCount = event.task_count - event.success_count;

  return (
    <div className={`${styles.card} ${isSuccess ? styles.success : styles.warning}`}>
      {/* Header */}
      <div className={styles.cardHeader}>
        <div>
          <h3>
            {isSuccess ? '✅' : '⚠️'} {event.text}
          </h3>
          <p className={styles.timestamp}>
            {new Date(event.timestamp * 1000).toLocaleTimeString()}
          </p>
        </div>

        {/* Voice Button */}
        {event.voice_attachment_url && (
          <button className={styles.voiceButton} onClick={onPlayVoice} title="Play voice summary">
            🎙️ Listen
          </button>
        )}
      </div>

      {/* Stats */}
      <div className={styles.stats}>
        <div className={styles.stat}>
          <span className={styles.label}>Total Tasks</span>
          <span className={styles.value}>{event.task_count}</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.label}>Successful</span>
          <span className={`${styles.value} ${styles.success}`}>{event.success_count}</span>
        </div>
        {failedCount > 0 && (
          <div className={styles.stat}>
            <span className={styles.label}>Failed</span>
            <span className={`${styles.value} ${styles.error}`}>{failedCount}</span>
          </div>
        )}
      </div>

      {/* Batch ID */}
      <div className={styles.batchId}>Batch ID: {event.batch_id}</div>
    </div>
  );
}
