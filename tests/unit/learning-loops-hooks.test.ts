import { renderHook, waitFor } from "@testing-library/react";
import { useLoops } from "@/hooks/use-learning-loops";
import { useDetail } from "@/hooks/use-learning-loops-detail";

global.fetch = jest.fn();

describe("useLoops", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("fetches loops on mount", async () => {
    const mockData = {
      loops: [
        { loop_id: "loop-1", plugin_id: "plugin-a", status: "active", health: { score: 0.95, trend: "up" }, event_count_7d: 42 },
      ],
    };
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockData,
    });

    const { result } = renderHook(() => useLoops());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.loops).toEqual(mockData.loops);
  });

  test("handles fetch error gracefully", async () => {
    (global.fetch as jest.Mock).mockRejectedValueOnce(new Error("Network error"));
    const { result } = renderHook(() => useLoops());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.error).toBeTruthy();
    expect(result.current.loops).toEqual([]);
  });

  test("handles 503 unavailable status", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 503,
    });
    const { result } = renderHook(() => useLoops());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.error).toContain("not available");
  });

  test("sets loading true on mount", () => {
    (global.fetch as jest.Mock).mockImplementation(() => new Promise(() => {}));
    const { result } = renderHook(() => useLoops());
    expect(result.current.loading).toBe(true);
  });

  test("polling interval set to 2 minutes", async () => {
    jest.useFakeTimers();
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ loops: [] }),
    });
    renderHook(() => useLoops());
    expect(global.fetch).toHaveBeenCalledTimes(1);
    jest.advanceTimersByTime(120000); // 2 minutes
    expect(global.fetch).toHaveBeenCalledTimes(2);
    jest.useRealTimers();
  });
});

describe("useDetail", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("fetches detail and events", async () => {
    const mockDetail = { health_trend: { points: [], min_score: 0.8, max_score: 1.0, avg_score: 0.9 } };
    const mockEvents = { events: [{ timestamp: new Date().toISOString(), event_type: "skill_executed" }] };

    (global.fetch as jest.Mock)
      .mockResolvedValueOnce({ ok: true, json: async () => mockDetail })
      .mockResolvedValueOnce({ ok: true, json: async () => mockEvents });

    const { result } = renderHook(() => useDetail("loop-1"));
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.trend).toEqual(mockDetail.health_trend);
    expect(result.current.events).toEqual(mockEvents.events);
  });

  test("handles empty loopId gracefully", () => {
    const { result } = renderHook(() => useDetail(""));
    expect(result.current.loading).toBe(true);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  test("handles fetch errors without crashing", async () => {
    (global.fetch as jest.Mock).mockRejectedValue(new Error("Fetch failed"));
    const { result } = renderHook(() => useDetail("loop-1"));
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.trend).toBeNull();
  });

  test("fetches with correct query params", async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ health_trend: {} }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ events: [] }) });

    renderHook(() => useDetail("test-loop-123"));
    await waitFor(() => {
      const calls = (global.fetch as jest.Mock).mock.calls;
      expect(calls[0][0]).toContain("test-loop-123/details");
      expect(calls[1][0]).toContain("test-loop-123/events");
      expect(calls[1][0]).toContain("limit=100");
    });
  });
});
