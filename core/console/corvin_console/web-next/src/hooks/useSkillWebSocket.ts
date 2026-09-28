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
 * - Auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s). The backoff
 *   resets only after a message arrives — an open that is closed straight away
 *   proves nothing. Close codes 4401 (no session) and 4501 (not implemented)
 *   are final: the hook reports them in `error` and stops reconnecting.
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
  OnConnectCallback,
  OnDisconnectCallback,
} from "@/types/websocket-events";

const RECONNECT_INTERVALS = [1000, 2000, 4000, 8000, 16000, 30000]; // ms
const HEARTBEAT_INTERVAL = 30000; // ms
const CONNECT_TIMEOUT = 5000; // ms
// Close codes the server uses for a final refusal (routes/learning_stream.py):
// retrying cannot change the answer, so these end the reconnect loop.
const TERMINAL_CLOSE_CODES: Record<number, string> = {
  4401: "no console session",
  4501: "stream not available on this build",
};

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
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout>>();
  const connectTimeoutRef = useRef<ReturnType<typeof setTimeout>>();
  const heartbeatIntervalRef = useRef<ReturnType<typeof setInterval>>();
  const subscriptionsRef = useRef<Map<SkillStreamId, ChannelSubscription>>(new Map());
  // Lifecycle guards. Without them the socket closed by the unmount cleanup
  // fired onclose, which scheduled a reconnect — the hook kept reconnecting
  // forever after its component was gone (and kept the test runner alive).
  const mountedRef = useRef(false);
  const attemptsRef = useRef(0);
  // Callbacks in refs so `connect` is stable: inline callbacks used to give
  // `connect` a new identity every render, and the mount effect (deps
  // [connect]) tore the socket down and reopened it on every render.
  const optsRef = useRef({ onConnect, onDisconnect, autoReconnect });
  optsRef.current = { onConnect, onDisconnect, autoReconnect };

  const clearTimers = () => {
    if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
    if (connectTimeoutRef.current) clearTimeout(connectTimeoutRef.current);
    if (heartbeatIntervalRef.current) clearInterval(heartbeatIntervalRef.current);
    reconnectTimeoutRef.current = undefined;
    connectTimeoutRef.current = undefined;
    heartbeatIntervalRef.current = undefined;
  };

  // Connect to WebSocket
  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) {
      return; // already connecting or open
    }

    const scheduleReconnect = () => {
      if (!mountedRef.current || !optsRef.current.autoReconnect) return;
      const delay = RECONNECT_INTERVALS[Math.min(attemptsRef.current, RECONNECT_INTERVALS.length - 1)];
      attemptsRef.current += 1;
      const attempts = attemptsRef.current;
      setState((prev) => ({ ...prev, isReconnecting: true, connectionAttempts: attempts }));
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = setTimeout(connect, delay);
    };

    let ws: WebSocket;
    try {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${protocol}//${window.location.host}/v1/console/learning/stream`);
    } catch (err) {
      setState((prev) => ({ ...prev, error: `Failed to connect: ${String(err)}` }));
      scheduleReconnect();
      return;
    }
    wsRef.current = ws;

    // A socket that never opens is closed, which routes it through onclose.
    connectTimeoutRef.current = setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) ws.close();
    }, CONNECT_TIMEOUT);

    ws.onopen = () => {
      if (connectTimeoutRef.current) clearTimeout(connectTimeoutRef.current);
      // Do NOT reset the backoff here: the server accepts and then closes
      // (4501) — resetting on open turned that into a reconnect every second.
      setState((prev) => ({
        ...prev,
        isConnected: true,
        isReconnecting: false,
        error: null,
      }));

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

      optsRef.current.onConnect?.();
    };

    ws.onmessage = (event) => {
      try {
        const msg: WebSocketEvent = JSON.parse(event.data);
        // A delivered message is what proves the stream works.
        attemptsRef.current = 0;
        setState((prev) => ({ ...prev, lastUpdate: msg, connectionAttempts: 0 }));
        if ("stream_id" in msg) {
          subscriptionsRef.current.get(msg.stream_id)?.callbacks.forEach((cb) => cb(msg));
        }
      } catch (err) {
        setState((prev) => ({ ...prev, error: `Failed to parse message: ${String(err)}` }));
      }
    };

    ws.onerror = () => {
      setState((prev) => ({ ...prev, error: "WebSocket connection error" }));
    };

    ws.onclose = (event: CloseEvent) => {
      if (wsRef.current !== ws) return; // a superseded socket
      wsRef.current = null;
      if (heartbeatIntervalRef.current) clearInterval(heartbeatIntervalRef.current);
      if (connectTimeoutRef.current) clearTimeout(connectTimeoutRef.current);
      if (!mountedRef.current) return;
      const terminal = TERMINAL_CLOSE_CODES[event?.code];
      if (terminal) {
        setState((prev) => ({
          ...prev,
          isConnected: false,
          isReconnecting: false,
          error: `Live updates unavailable (${event.code}: ${terminal})`,
        }));
        optsRef.current.onDisconnect?.();
        return;
      }
      setState((prev) => ({ ...prev, isConnected: false }));
      optsRef.current.onDisconnect?.();
      scheduleReconnect();
    };
  }, []);

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
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      clearTimers();
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        ws.close();
      }
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
  const { subscribe, unsubscribe: _unsubscribe, ...state } = useSkillWebSocket({
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
