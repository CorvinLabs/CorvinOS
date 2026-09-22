/**
 * Tests for Snapshots Manager Panel (control-snapshots.tsx)
 * 9 component tests covering snapshot creation, restore, and integrity verification
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@/components/ui/toast";
import ControlSnapshotsPage from "@/pages/control-snapshots";
import * as React from "react";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

const mockSnapshotsResponse = {
  timestamp: "2026-09-22T12:00:00Z",
  total_snapshots: 3,
  total_size_mb: 1024.5,
  snapshots: [
    {
      snapshot_id: "snap-001",
      name: "Pre-release-2026-09-22",
      description: "System state before major release",
      created_by: "automation-user",
      created_at: "2026-09-22T10:00:00Z",
      size_bytes: 536870912,
      state_hash: "abc123def456ghi789jkl012mno345p",
      integrity_status: "valid",
      tags: ["production", "pre-release"],
      state_summary: {
        subsystems_count: 8,
        plugins_enabled: 12,
        config_entries: 256,
        events_captured: 5432,
      },
    },
    {
      snapshot_id: "snap-002",
      name: "Testing-baseline-2026-09-20",
      description: "Validated baseline for testing",
      created_by: "test-runner",
      created_at: "2026-09-20T14:30:00Z",
      size_bytes: 268435456,
      state_hash: "xyz789abc123def456ghi789jkl012m",
      integrity_status: "warning",
      tags: ["testing"],
      state_summary: {
        subsystems_count: 8,
        plugins_enabled: 10,
        config_entries: 248,
        events_captured: 3215,
      },
    },
  ],
};

const Wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={queryClient}>
    <ToastProvider>{children}</ToastProvider>
  </QueryClientProvider>
);

describe("Snapshots Manager Page", () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it("should render page header and description", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSnapshotsResponse,
    });

    render(<ControlSnapshotsPage />, { wrapper: Wrapper });

    expect(screen.getByText("Snapshots Manager")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Create, restore, and manage state snapshots/
      )
    ).toBeInTheDocument();
  });

  it("should display storage summary with total snapshots and size", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSnapshotsResponse,
    });

    render(<ControlSnapshotsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Total Snapshots")).toBeInTheDocument();
      expect(screen.getByText("Storage Used")).toBeInTheDocument();
    });
  });

  it("should allow creating new snapshots via dialog", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSnapshotsResponse,
    });

    render(<ControlSnapshotsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const createButton = screen.getByText("Create Snapshot");
      fireEvent.click(createButton);
      expect(screen.getByText("Create New Snapshot")).toBeInTheDocument();
    });
  });

  it("should display snapshot cards with integrity status badges", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSnapshotsResponse,
    });

    render(<ControlSnapshotsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("Pre-release-2026-09-22")).toBeInTheDocument();
      expect(screen.getByText("Testing-baseline-2026-09-20")).toBeInTheDocument();
    });
  });

  it("should show state summary for each snapshot", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSnapshotsResponse,
    });

    render(<ControlSnapshotsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const subsystemCounts = screen.getAllByText("Subsystems");
      expect(subsystemCounts.length).toBeGreaterThan(0);
      expect(screen.getByText("Plugins")).toBeInTheDocument();
    });
  });

  it("should allow restoring snapshots", async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSnapshotsResponse,
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      });

    render(<ControlSnapshotsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const restoreButtons = screen.getAllByLabelText("Restore snapshot");
      expect(restoreButtons.length).toBeGreaterThan(0);
    });
  });

  it("should allow verifying snapshot integrity", async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSnapshotsResponse,
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      });

    render(<ControlSnapshotsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const verifyButtons = screen.getAllByLabelText("Verify snapshot integrity");
      expect(verifyButtons.length).toBeGreaterThan(0);
    });
  });

  it("should display snapshot tags and metadata", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockSnapshotsResponse,
    });

    render(<ControlSnapshotsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByText("production")).toBeInTheDocument();
      expect(screen.getByText("testing")).toBeInTheDocument();
    });
  });

  it("should allow deleting snapshots", async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSnapshotsResponse,
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      });

    render(<ControlSnapshotsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      const deleteButtons = screen.getAllByLabelText("Delete snapshot");
      expect(deleteButtons.length).toBeGreaterThan(0);
    });
  });
});
