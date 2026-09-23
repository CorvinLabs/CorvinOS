/**
 * useSkillWebSocket Hook — Real-Time Skill Updates (Phase 3a, ADR-2050)
 *
 * Usage:
 *   const { isConnected, subscribe, unsubscribe } = useSkillWebSocket();
 *
 *   useEffect(() => {
 *     subscribe("workflow-optimizer", (event) => {
 *       if (isConfidenceUpdate(event)) {
 *         setConfidence(event.data.new_confidence);
 *       }
 *     });
 *   }, []);
 *
 * Features:
 * - Auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s)
 * - Per-channel subscriptions
 * - Heartbeat (ping/pong every 30s)
 * - Error recovery (graceful degradation)
 */

import { useEffect, useState, useCallback, useRef } from "react";
import type {
  WebSocketEvent,
  SkillStreamId,
  SubscribeMessage,
  UnsubscribeMessage,
  PingMessage,
  SkillStreamState,
  OnUpdateCallback,
  OnErrorCallback,
  OnConnectCallback,
  OnDisconnectCallback,
} from "@/types/websocket-events";

const RECONNECT_INTERVALS = [1000, 2000, 4000, 8000, 16000, 30000]; // ms
const HEARTBEAT_INTERVAL = 30000; // ms
const CONNECT_TIMEOUT = 5000; // ms

interface UseSkillWebSocketOptions {
  onConnect?: OnConnectCallback;
  onDisconnect?: OnDisconnectCallback;
  autoReconnect?: boolean;
}

interface ChannelSubscription {
  callbacks: Set<OnUpdateCallback>;
}

export function useSkillWebSocket(options: UseSkillWebSocketOptions = {}) {
  const { onConnect, onDisconnect, autoReconnect = true } = options;

  // State
  const [state, setState] = useState<SkillStreamState>({
    isConnected: false,
    subscribedChannels: new Set(),
    lastUpdate: null,
    error: null,
    isReconnecting: false,
    connectionAttempts: 0,
  });

  // Refs
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout>();
  const heartbeatIntervalRef = useRef<NodeJS.Timeout>();
  const subscriptionsRef = useRef<Map<SkillStreamId, ChannelSubscription>>(new Map());
  const connectionPromiseRef = useRef<Promise<void> | null>(null);
  const connectionResolveRef = useRef<(() => void) | null>(null);

  // Connect to WebSocket
  const connect = useCallback(async () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    try {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/v1/console/learning/stream`;

      const ws = new WebSocket(url);

      // Set connection timeout
      const connectionTimeout = new Promise<never>((_, reject) => {
        setTimeout(() => reject(new Error("Connection timeout")), CONNECT_TIMEOUT);
      });

      ws.onopen = () => {
        console.log("[Skill WebSocket] Connected");
        setState((prev) => ({
          ...prev,
          isConnected: true,
          isReconnecting: false,
          error: null,
          connectionAttempts: 0,
        }));

        // Start heartbeat
        if (heartbeatIntervalRef.current) clearInterval(heartbeatIntervalRef.current);
        heartbeatIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            const msg: PingMessage = { action: "ping" };
            ws.send(JSON.stringify(msg));
          }
        }, HEARTBEAT_INTERVAL);

        // Re-subscribe to all channels
        subscriptionsRef.current.forEach((_, channel) => {
          const msg: SubscribeMessage = { action: "subscribe", channel };
          ws.send(JSON.stringify(msg));
        });

        // Resolve connection promise
        if (connectionResolveRef.current) {
          connectionResolveRef.current();
          connectionResolveRef.current = null;
        }

        onConnect?.();
      };

      ws.onmessage = (event) => {
        try {
          const msg: WebSocketEvent = JSON.parse(event.data);

          setState((prev) => ({
            ...prev,
            lastUpdate: msg,
          }));

          // Dispatch to all callbacks subscribed to this channel
          if ("stream_id" in msg) {
            const channel = msg.stream_id;
            const subscription = subscriptionsRef.current.get(channel);
            if (subscription) {
              subscription.callbacks.forEach((cb) => cb(msg));
            }
          }
        } catch (err) {
          console.error("[Skill WebSocket] Parse error:", err);
          setState((prev) => ({
            ...prev,
            error: `Failed to parse message: ${String(err)}`,
          }));
        }
      };

      ws.onerror = (event) => {
        console.error("[Skill WebSocket] Error:", event);
        setState((prev) => ({
          ...prev,
          error: "WebSocket connection error",
        }));
      };

      ws.onclose = () => {
        console.log("[Skill WebSocket] Disconnected");

        // Clear heartbeat
        if (heartbeatIntervalRef.current) {
          clearInterval(heartbeatIntervalRef.current);
        }

        setState((prev) => ({
          ...prev,
          isConnected: false,
        }));

        onDisconnect?.();

        // Auto-reconnect
        if (autoReconnect) {
          setState((prev) => {
            const nextAttempt = Math.min(
              prev.connectionAttempts,
              RECONNECT_INTERVALS.length - 1
            );
            const delay = RECONNECT_INTERVALS[nextAttempt];

            console.log(`[Skill WebSocket] Reconnecting in ${delay}ms...`);

            reconnectTimeoutRef.current = setTimeout(connect, delay);

            return {
              ...prev,
              isReconnecting: true,
              connectionAttempts: prev.connectionAttempts + 1,
            };
          });
        }
      };

      wsRef.current = ws;

      // Wait for connection (or timeout)
      await Promise.race([
        new Promise<void>((resolve) => {
          connectionResolveRef.current = resolve;
        }),
        connectionTimeout,
      ]);
    } catch (err) {
      console.error("[Skill WebSocket] Connection failed:", err);
      setState((prev) => ({
        ...prev,
        error: `Failed to connect: ${String(err)}`,
        isReconnecting: autoReconnect,
      }));

      if (autoReconnect) {
        setState((prev) => {
          const nextAttempt = Math.min(
            prev.connectionAttempts,
            RECONNECT_INTERVALS.length - 1
          );
          const delay = RECONNECT_INTERVALS[nextAttempt];

          reconnectTimeoutRef.current = setTimeout(connect, delay);

          return {
            ...prev,
            connectionAttempts: prev.connectionAttempts + 1,
          };
        });
      }
    }
  }, [autoReconnect, onConnect, onDisconnect]);

  // Subscribe to channel
  const subscribe = useCallback(
    (channel: SkillStreamId, onUpdate: OnUpdateCallback) => {
      // Add callback
      if (!subscriptionsRef.current.has(channel)) {
        subscriptionsRef.current.set(channel, { callbacks: new Set() });
      }

      const subscription = subscriptionsRef.current.get(channel)!;
      subscription.callbacks.add(onUpdate);

      // Update state
      setState((prev) => ({
        ...prev,
        subscribedChannels: new Set([...prev.subscribedChannels, channel]),
      }));

      // Send subscription message if connected
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        const msg: SubscribeMessage = { action: "subscribe", channel };
        wsRef.current.send(JSON.stringify(msg));
      }

      // Return unsubscribe function
      return () => unsubscribe(channel, onUpdate);
    },
    []
  );

  // Unsubscribe from channel
  const unsubscribe = useCallback((channel: SkillStreamId, onUpdate: OnUpdateCallback) => {
    const subscription = subscriptionsRef.current.get(channel);
    if (subscription) {
      subscription.callbacks.delete(onUpdate);

      // If no more callbacks, remove subscription
      if (subscription.callbacks.size === 0) {
        subscriptionsRef.current.delete(channel);

        setState((prev) => ({
          ...prev,
          subscribedChannels: new Set(
            [...prev.subscribedChannels].filter((c) => c !== channel)
          ),
        }));

        // Send unsubscribe message if connected
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          const msg: UnsubscribeMessage = { action: "unsubscribe", channel };
          wsRef.current.send(JSON.stringify(msg));
        }
      }
    }
  }, []);

  // Connect on mount
  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (heartbeatIntervalRef.current) {
        clearInterval(heartbeatIntervalRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);

  return {
    ...state,
    subscribe,
    unsubscribe,
    reconnect: connect,
  };
}

/**
 * Hook for listening to a specific stream channel
 */
export function useSkillStream(
  channel: SkillStreamId,
  onUpdate?: OnUpdateCallback,
  autoConnect = true
) {
  const { subscribe, unsubscribe, ...state } = useSkillWebSocket({
    autoReconnect: autoConnect,
  });

  const [updates, setUpdates] = useState<WebSocketEvent[]>([]);

  useEffect(() => {
    if (!onUpdate && !autoConnect) {
      return;
    }

    const handleUpdate = (event: WebSocketEvent) => {
      setUpdates((prev) => [...prev, event].slice(-100)); // Keep last 100
      onUpdate?.(event);
    };

    const unsubscribeFn = subscribe(channel, handleUpdate);

    return unsubscribeFn;
  }, [channel, onUpdate, subscribe, autoConnect]);

  return {
    ...state,
    updates,
  };
}
