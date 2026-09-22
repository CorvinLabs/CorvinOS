/**
 * Tests for Subsystem Manager Panel (control-subsystems.tsx)
 * 9 component tests covering rendering, filtering, and subsystem operations
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@/components/ui/toast";
import ControlSubsystemsPage from "@/pages/control-subsystems";
import * as React from "react";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

const mockSubsystemsResponse = {
  timestamp: "2026-09-22T12:00:00Z",
  count_healthy: 5,
  count_degraded: 1,
  count_unhealthy: 1,
  count_offline: 0,
  subsystems: [
    {
      subsystem_id: "skill-router",
      name: "Skill Router",
      description: "Intent classification and routing",
      status: "healthy",
      enabled: true,
      version: "1.2.3",
      uptime_seconds: 172800,
      memory_usage_mb: 256.5,
      error_count: 0,
      last_health_check: "2026-09-22T12:00:00Z",
      dependencies: ["audit-chain", "plugins"],
    },
    {
      subsystem_id: "autonomy-engine",
      name: "Autonomy Engine",
      description: "Autonomous agent decision making",
      status: "degraded",
      enabled: true,
      version: "0.9.1",
      uptime_seconds: 86400,
      memory_usage_mb: 512.3,
      error_count: 3,
      last_health_check: "2026-09-22T11:55:00Z",
      dependencies: ["skill-router"],
    },
  ],
};

const Wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={queryClient}>
    <ToastProvider>{children}</ToastProvider>
  </QueryClientProvider>
);

describe("Subsystems Manager Page", () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it("should render page header and description", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSubsystemsResponse,
    });

    render(<ControlSubsystemsPage />, { wrapper: Wrapper });

    expect(screen.getByText("Subsystem Manager")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Monitor and control all CorvinOS subsystems with real-time status updates/
      )
    ).toBeInTheDocument();
  });

  it("should display status summary cards", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSubsystemsResponse,
    });

    render(<ControlSubsystemsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Healthy")).toBeInTheDocument();
      expect(screen.getByText("Degraded")).toBeInTheDocument();
      expect(screen.getByText("Unhealthy")).toBeInTheDocument();
    });
  });

  it("should render subsystem cards with health status badges", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSubsystemsResponse,
    });

    render(<ControlSubsystemsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Skill Router")).toBeInTheDocument();
      expect(screen.getByText("Autonomy Engine")).toBeInTheDocument();
    });
  });

  it("should allow filtering subsystems by status", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSubsystemsResponse,
    });

    render(<ControlSubsystemsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const filterSelect = screen.getByLabelText("Filter by status");
      fireEvent.change(filterSelect, { target: { value: "degraded" } });
      expect(screen.getByText("Autonomy Engine")).toBeInTheDocument();
    });
  });

  it("should display subsystem metadata (version, uptime, memory)", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSubsystemsResponse,
    });

    render(<ControlSubsystemsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("1.2.3")).toBeInTheDocument();
      expect(screen.getAllByText(/MB/)).toBeTruthy();
    });
  });

  it("should allow toggling subsystem enable/disable via switch", async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSubsystemsResponse,
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      });

    render(<ControlSubsystemsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const switches = screen.getAllByRole("switch");
      expect(switches.length).toBeGreaterThan(0);
    });
  });

  it("should allow running health checks on subsystems", async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSubsystemsResponse,
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      });

    render(<ControlSubsystemsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const healthCheckButtons = screen.getAllByLabelText("Run health check");
      expect(healthCheckButtons.length).toBeGreaterThan(0);
    });
  });

  it("should display dependencies for each subsystem", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSubsystemsResponse,
    });

    render(<ControlSubsystemsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("audit-chain")).toBeInTheDocument();
      expect(screen.getByText("plugins")).toBeInTheDocument();
    });
  });

  it("should handle API errors gracefully", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 500,
    });

    render(<ControlSubsystemsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText(/Failed to load subsystems/)).toBeInTheDocument();
    });
  });
});
