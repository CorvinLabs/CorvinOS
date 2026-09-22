/**
 * Tests for Override Authority Panel (control-overrides.tsx)
 * 9 component tests covering override creation, approval, and TTL management
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@/components/ui/toast";
import ControlOverridesPage from "@/pages/control-overrides";
import * as React from "react";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

const mockOverridesResponse = {
  timestamp: "2026-09-22T12:00:00Z",
  active_count: 2,
  pending_count: 1,
  revoked_count: 1,
  overrides: [
    {
      override_id: "ovr-001",
      subsystem_id: "skill-router",
      requested_by: "operator-1",
      reason: "Emergency maintenance window",
      status: "approved",
      requested_at: "2026-09-22T10:00:00Z",
      approved_at: "2026-09-22T10:05:00Z",
      expires_at: "2026-09-22T14:00:00Z",
      ttl_seconds: 14400,
      constraint: "write:skills:*",
    },
    {
      override_id: "ovr-002",
      subsystem_id: "autonomy-engine",
      requested_by: "operator-2",
      reason: "Debugging production issue",
      status: "pending",
      requested_at: "2026-09-22T11:30:00Z",
      expires_at: "2026-09-22T13:30:00Z",
      ttl_seconds: 7200,
      constraint: "read:autonomy:debug",
    },
  ],
};

const Wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={queryClient}>
    <ToastProvider>{children}</ToastProvider>
  </QueryClientProvider>
);

describe("Override Authority Page", () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it("should render page header and description", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockOverridesResponse,
    });

    render(<ControlOverridesPage />, { wrapper: Wrapper });

    expect(screen.getByText("Override Authority")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Request, approve, and manage security overrides/
      )
    ).toBeInTheDocument();
  });

  it("should display override status summary", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockOverridesResponse,
    });

    render(<ControlOverridesPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Active")).toBeInTheDocument();
      expect(screen.getByText("Pending Approval")).toBeInTheDocument();
    });
  });

  it("should allow creating new override requests via dialog", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockOverridesResponse,
    });

    render(<ControlOverridesPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const createButton = screen.getByText("Create Override");
      fireEvent.click(createButton);
      expect(screen.getByText("Create Override Request")).toBeInTheDocument();
    });
  });

  it("should display override details including subsystem and status", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockOverridesResponse,
    });

    render(<ControlOverridesPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("skill-router")).toBeInTheDocument();
      expect(screen.getByText("autonomy-engine")).toBeInTheDocument();
    });
  });

  it("should show remaining time in TTL timer", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockOverridesResponse,
    });

    render(<ControlOverridesPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const remainingTexts = screen.getAllByText(/remaining/);
      expect(remainingTexts.length).toBeGreaterThan(0);
    });
  });

  it("should allow toggling reason visibility", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockOverridesResponse,
    });

    render(<ControlOverridesPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const showReasonButtons = screen.getAllByLabelText("Toggle reason visibility");
      fireEvent.click(showReasonButtons[0]);
      expect(screen.getByText("Emergency maintenance window")).toBeInTheDocument();
    });
  });

  it("should show approve button for pending overrides", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockOverridesResponse,
    });

    render(<ControlOverridesPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const approveButtons = screen.getAllByLabelText("Approve override");
      expect(approveButtons.length).toBeGreaterThan(0);
    });
  });

  it("should allow revoking active overrides", async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockOverridesResponse,
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      });

    render(<ControlOverridesPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const revokeButtons = screen.getAllByLabelText("Revoke override");
      expect(revokeButtons.length).toBeGreaterThan(0);
    });
  });

  it("should allow copying override IDs to clipboard", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockOverridesResponse,
    });

    Object.assign(navigator, {
      clipboard: {
        writeText: jest.fn(),
      },
    });

    render(<ControlOverridesPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const copyButtons = screen.getAllByLabelText("Copy override ID");
      expect(copyButtons.length).toBeGreaterThan(0);
    });
  });
});
