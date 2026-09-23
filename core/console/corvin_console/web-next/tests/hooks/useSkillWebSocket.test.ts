/**
 * Hook Tests for useSkillWebSocket (Phase 3a, ADR-2050)
 *
 * Tests:
 *   1. Hook initialization and cleanup
 *   2. Subscribe/unsubscribe lifecycle
 *   3. Callback invocation on updates
 *
 * Run: npm run test:unit -- useSkillWebSocket.test.ts
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useSkillWebSocket, useSkillStream } from "@/hooks/useSkillWebSocket";
import type { WebSocketEvent } from "@/types/websocket-events";

describe("useSkillWebSocket Hook", () => {
  let mockWebSocket: any;

  beforeEach(() => {
    // Mock WebSocket
    mockWebSocket = {
      readyState: WebSocket.CONNECTING,
      send: vi.fn(),
      close: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      onopen: null,
      onclose: null,
      onerror: null,
      onmessage: null,
    };

    global.WebSocket = vi.fn(() => mockWebSocket) as any;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // ========================================================================
  // Test 1: Initialize and Cleanup
  // ========================================================================
  it("should initialize with default state", () => {
    const { result } = renderHook(() => useSkillWebSocket());

    expect(result.current.isConnected).toBe(false);
    expect(result.current.error).toBe(null);
    expect(result.current.subscribedChannels.size).toBe(0);
  });

  it("should cleanup WebSocket on unmount", () => {
    const { unmount } = renderHook(() => useSkillWebSocket());

    // Simulate connection
    act(() => {
      mockWebSocket.readyState = WebSocket.OPEN;
      mockWebSocket.onopen?.();
    });

    unmount();

    expect(mockWebSocket.close).toHaveBeenCalled();
  });

  // ========================================================================
  // Test 2: Subscribe/Unsubscribe Lifecycle
  // ========================================================================
  it("should subscribe to a channel", async () => {
    const { result } = renderHook(() => useSkillWebSocket());

    // Simulate connection
    act(() => {
      mockWebSocket.readyState = WebSocket.OPEN;
      mockWebSocket.onopen?.();
    });

    await waitFor(() => expect(result.current.isConnected).toBe(true));

    // Subscribe to channel
    const callback = vi.fn();

    act(() => {
      result.current.subscribe("workflow-optimizer", callback);
    });

    expect(result.current.subscribedChannels.has("workflow-optimizer")).toBe(true);
    expect(mockWebSocket.send).toHaveBeenCalled();

    // Check subscription message format
    const sendCall = (mockWebSocket.send as any).mock.calls[0]?.[0];
    if (sendCall) {
      const msg = JSON.parse(sendCall);
      expect(msg.action).toBe("subscribe");
      expect(msg.channel).toBe("workflow-optimizer");
    }
  });

  it("should unsubscribe from a channel", async () => {
    const { result } = renderHook(() => useSkillWebSocket());

    act(() => {
      mockWebSocket.readyState = WebSocket.OPEN;
      mockWebSocket.onopen?.();
    });

    await waitFor(() => expect(result.current.isConnected).toBe(true));

    const callback = vi.fn();

    act(() => {
      result.current.subscribe("security-orchestrator", callback);
    });

    expect(result.current.subscribedChannels.has("security-orchestrator")).toBe(true);

    // Unsubscribe
    act(() => {
      result.current.unsubscribe("security-orchestrator", callback);
    });

    expect(result.current.subscribedChannels.has("security-orchestrator")).toBe(false);
  });

  // ========================================================================
  // Test 3: Callback Invocation
  // ========================================================================
  it("should invoke callback on confidence_updated event", async () => {
    const { result } = renderHook(() => useSkillWebSocket());

    act(() => {
      mockWebSocket.readyState = WebSocket.OPEN;
      mockWebSocket.onopen?.();
    });

    await waitFor(() => expect(result.current.isConnected).toBe(true));

    const callback = vi.fn();

    act(() => {
      result.current.subscribe("workflow-optimizer", callback);
    });

    // Simulate confidence update
    const event: WebSocketEvent = {
      type: "confidence_updated",
      stream_id: "workflow-optimizer",
      data: {
        new_confidence: 0.85,
        version: 2,
        timestamp: new Date().toISOString(),
      },
      timestamp: new Date().toISOString(),
    };

    act(() => {
      mockWebSocket.onmessage?.({
        data: JSON.stringify(event),
      });
    });

    expect(callback).toHaveBeenCalledWith(event);
  });

  it("should dispatch to correct channel callback", async () => {
    const { result } = renderHook(() => useSkillWebSocket());

    act(() => {
      mockWebSocket.readyState = WebSocket.OPEN;
      mockWebSocket.onopen?.();
    });

    await waitFor(() => expect(result.current.isConnected).toBe(true));

    const callback1 = vi.fn();
    const callback2 = vi.fn();

    act(() => {
      result.current.subscribe("workflow-optimizer", callback1);
      result.current.subscribe("security-orchestrator", callback2);
    });

    // Send event to workflow-optimizer
    const event: WebSocketEvent = {
      type: "confidence_updated",
      stream_id: "workflow-optimizer",
      data: {
        new_confidence: 0.75,
        version: 1,
        timestamp: new Date().toISOString(),
      },
      timestamp: new Date().toISOString(),
    };

    act(() => {
      mockWebSocket.onmessage?.({
        data: JSON.stringify(event),
      });
    });

    expect(callback1).toHaveBeenCalledWith(event);
    expect(callback2).not.toHaveBeenCalled();
  });

  // ========================================================================
  // Test 4: Reconnection
  // ========================================================================
  it("should attempt reconnection on disconnect", async () => {
    vi.useFakeTimers();

    const { result } = renderHook(() => useSkillWebSocket({ autoReconnect: true }));

    act(() => {
      mockWebSocket.readyState = WebSocket.OPEN;
      mockWebSocket.onopen?.();
    });

    await waitFor(() => expect(result.current.isConnected).toBe(true));

    // Simulate disconnect
    act(() => {
      mockWebSocket.readyState = WebSocket.CLOSED;
      mockWebSocket.onclose?.();
    });

    expect(result.current.isReconnecting).toBe(true);

    vi.useRealTimers();
  });
});

describe("useSkillStream Hook", () => {
  let mockWebSocket: any;

  beforeEach(() => {
    mockWebSocket = {
      readyState: WebSocket.CONNECTING,
      send: vi.fn(),
      close: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      onopen: null,
      onclose: null,
      onerror: null,
      onmessage: null,
    };

    global.WebSocket = vi.fn(() => mockWebSocket) as any;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // ========================================================================
  // Test 5: useSkillStream Convenience Hook
  // ========================================================================
  it("should subscribe to channel automatically", async () => {
    const { result } = renderHook(() => useSkillStream("workflow-optimizer"));

    act(() => {
      mockWebSocket.readyState = WebSocket.OPEN;
      mockWebSocket.onopen?.();
    });

    await waitFor(() => expect(result.current.isConnected).toBe(true));
  });

  it("should collect updates in array", async () => {
    const { result } = renderHook(() => useSkillStream("workflow-optimizer"));

    act(() => {
      mockWebSocket.readyState = WebSocket.OPEN;
      mockWebSocket.onopen?.();
    });

    await waitFor(() => expect(result.current.isConnected).toBe(true));

    const event: WebSocketEvent = {
      type: "confidence_updated",
      stream_id: "workflow-optimizer",
      data: {
        new_confidence: 0.9,
        version: 3,
        timestamp: new Date().toISOString(),
      },
      timestamp: new Date().toISOString(),
    };

    act(() => {
      mockWebSocket.onmessage?.({
        data: JSON.stringify(event),
      });
    });

    await waitFor(() => expect(result.current.updates.length).toBeGreaterThan(0));
  });
});
