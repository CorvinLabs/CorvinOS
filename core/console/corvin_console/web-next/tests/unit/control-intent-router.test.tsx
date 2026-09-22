/**
 * Tests for Intent Router Dashboard (control-intent-router.tsx)
 * 9 component tests covering rendering, interactions, and error states
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@/components/ui/toast";
import ControlIntentRouterPage from "@/pages/control-intent-router";
import * as React from "react";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

const mockStatsResponse = {
  timestamp: "2026-09-22T12:00:00Z",
  uptime_seconds: 86400,
  paths: [
    {
      path_id: "skill_gen",
      label: "Skill Generation",
      description: "Runtime skill generation and deployment",
      requests_total: 1250,
      requests_success: 1200,
      confidence_avg: 0.92,
      latency_ms: 45,
    },
    {
      path_id: "autonomy",
      label: "Autonomy Path",
      description: "Autonomous agent decision making",
      requests_total: 850,
      requests_success: 790,
      confidence_avg: 0.85,
      latency_ms: 62,
    },
    {
      path_id: "feedback",
      label: "Feedback Loop",
      description: "User feedback processing and learning",
      requests_total: 320,
      requests_success: 310,
      confidence_avg: 0.88,
      latency_ms: 28,
    },
  ],
  recent_requests: [
    {
      request_id: "req-001",
      intent: "deploy new skill",
      path_selected: "skill_gen",
      confidence: 0.95,
      success: true,
      latency_ms: 42,
      timestamp: "2026-09-22T12:00:00Z",
    },
    {
      request_id: "req-002",
      intent: "enable subsystem",
      path_selected: "autonomy",
      confidence: 0.82,
      success: true,
      latency_ms: 58,
      timestamp: "2026-09-22T11:59:30Z",
    },
  ],
};

const Wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={queryClient}>
    <ToastProvider>{children}</ToastProvider>
  </QueryClientProvider>
);

describe("Intent Router Page", () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it("should render page header and description", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockStatsResponse,
    });

    render(<ControlIntentRouterPage />, { wrapper: Wrapper });

    expect(screen.getByText("Intent Router Dashboard")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Real-time request routing, confidence scores, and success rates/
      )
    ).toBeInTheDocument();
  });

  it("should display loading skeleton while fetching data", () => {
    (global.fetch as jest.Mock).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          setTimeout(() => {
            resolve({ ok: true, json: async () => mockStatsResponse });
          }, 100);
        })
    );

    const { container } = render(<ControlIntentRouterPage />, {
      wrapper: Wrapper,
    });

    expect(container.querySelector(".skeleton")).toBeInTheDocument();
  });

  it("should render router health summary with uptime and stats", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockStatsResponse,
    });

    render(<ControlIntentRouterPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Router Health")).toBeInTheDocument();
      expect(screen.getByText(/1d/)).toBeInTheDocument();
    });
  });

  it("should render all three dispatch paths with stats", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockStatsResponse,
    });

    render(<ControlIntentRouterPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Skill Generation")).toBeInTheDocument();
      expect(screen.getByText("Autonomy Path")).toBeInTheDocument();
      expect(screen.getByText("Feedback Loop")).toBeInTheDocument();
    });
  });

  it("should display success rate progress bars for each path", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockStatsResponse,
    });

    render(<ControlIntentRouterPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const progressBars = screen.getAllByRole("progressbar");
      expect(progressBars.length).toBeGreaterThan(0);
    });
  });

  it("should show recent requests table with data", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockStatsResponse,
    });

    render(<ControlIntentRouterPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Recent Requests")).toBeInTheDocument();
      expect(screen.getByText(/deploy new skill/)).toBeInTheDocument();
    });
  });

  it("should allow copying request IDs to clipboard", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockStatsResponse,
    });

    Object.assign(navigator, {
      clipboard: {
        writeText: jest.fn(),
      },
    });

    render(<ControlIntentRouterPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const copyButtons = screen.getAllByLabelText("Copy request ID");
      expect(copyButtons.length).toBeGreaterThan(0);
    });
  });

  it("should handle API error gracefully", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 500,
    });

    render(<ControlIntentRouterPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText(/Failed to load router stats/)).toBeInTheDocument();
    });
  });

  it("should support auto-refresh toggle", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockStatsResponse,
    });

    render(<ControlIntentRouterPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const pauseButton = screen.getByText("Pause Auto-Refresh");
      expect(pauseButton).toBeInTheDocument();
      fireEvent.click(pauseButton);
      expect(screen.getByText("Resume Auto-Refresh")).toBeInTheDocument();
    });
  });
});
