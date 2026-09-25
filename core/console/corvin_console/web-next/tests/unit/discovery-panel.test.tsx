/**
 * Unit tests for Discovery Panel components.
 *
 * k=1 Gate tests:
 * - StatusBadge rendering
 * - PeerList empty state
 * - PeerList with peers
 * - PeerDetail modal
 * - Search filtering
 * - Accessibility (ARIA labels, keyboard nav)
 */

import React from "react";
import { render, screen, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { DiscoveryPanel } from "@/pages/discovery";
import * as a2aApi from "@/lib/api/a2a";

// Mock the API
vi.mock("@/lib/api/a2a");

// Wrapper component for tests
const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
};

describe("DiscoveryPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading state initially", () => {
    vi.mocked(a2aApi.listDiscoveryPeers).mockImplementationOnce(
      () => new Promise(() => {}), // Never resolves
    );

    render(
      <TestWrapper>
        <DiscoveryPanel />
      </TestWrapper>,
    );

    expect(screen.getByText("Peer Discovery")).toBeInTheDocument();
  });

  it("renders empty state when no peers discovered", async () => {
    vi.mocked(a2aApi.listDiscoveryPeers).mockResolvedValueOnce({
      peers: [],
      total: 0,
    });

    render(
      <TestWrapper>
        <DiscoveryPanel />
      </TestWrapper>,
    );

    // Wait for data to load
    await screen.findByText("No Peers Discovered");
    expect(screen.getByText(/Connect with other CorvinOS instances/i)).toBeInTheDocument();
  });

  it("renders peer list with correct data", async () => {
    const mockPeers = [
      {
        peer_id: "peer-1",
        name: "Peer One",
        status: "online",
        region: "us-east-1",
        endpoint: "http://peer1.example.com",
        instance_id: "inst-123",
        last_seen: "2026-09-24T12:00:00Z",
      },
      {
        peer_id: "peer-2",
        name: "Peer Two",
        status: "offline",
        region: "eu-west-1",
        endpoint: "http://peer2.example.com",
        instance_id: "inst-456",
        last_seen: "2026-09-23T12:00:00Z",
      },
    ];

    vi.mocked(a2aApi.listDiscoveryPeers).mockResolvedValueOnce({
      peers: mockPeers,
      total: 2,
    });

    render(
      <TestWrapper>
        <DiscoveryPanel />
      </TestWrapper>,
    );

    // Wait for peers to load
    await screen.findByText("Peer One");
    expect(screen.getByText("Peer Two")).toBeInTheDocument();

    // Check status badges
    const onlineBadges = screen.getAllByText("Online");
    expect(onlineBadges.length).toBeGreaterThan(0);

    const offlineBadges = screen.getAllByText("Offline");
    expect(offlineBadges.length).toBeGreaterThan(0);
  });

  it("filters peers by search term", async () => {
    const mockPeers = [
      {
        peer_id: "peer-1",
        name: "Alice",
        status: "online",
        region: "us-east-1",
        endpoint: null,
        instance_id: "inst-123",
        last_seen: null,
      },
      {
        peer_id: "peer-2",
        name: "Bob",
        status: "online",
        region: "eu-west-1",
        endpoint: null,
        instance_id: "inst-456",
        last_seen: null,
      },
    ];

    vi.mocked(a2aApi.listDiscoveryPeers).mockResolvedValueOnce({
      peers: mockPeers,
      total: 2,
    });

    render(
      <TestWrapper>
        <DiscoveryPanel />
      </TestWrapper>,
    );

    // Wait for peers to load
    await screen.findByText("Alice");

    // Search for "Alice"
    const searchInput = screen.getByPlaceholderText(/Search peers/i);
    await userEvent.type(searchInput, "Alice");

    // Alice should be visible
    expect(screen.getByText("Alice")).toBeInTheDocument();

    // Bob should not be visible
    expect(screen.queryByText("Bob")).not.toBeInTheDocument();
  });

  it("opens peer detail modal on row click", async () => {
    const mockPeers = [
      {
        peer_id: "peer-1",
        name: "Test Peer",
        status: "online",
        region: "us-east-1",
        endpoint: "http://test.example.com",
        instance_id: "inst-123",
        last_seen: "2026-09-24T12:00:00Z",
      },
    ];

    vi.mocked(a2aApi.listDiscoveryPeers).mockResolvedValueOnce({
      peers: mockPeers,
      total: 1,
    });

    render(
      <TestWrapper>
        <DiscoveryPanel />
      </TestWrapper>,
    );

    // Wait for peers to load
    await screen.findByText("Test Peer");

    // Click on "Details" button
    const detailsButton = screen.getByText("Details");
    fireEvent.click(detailsButton);

    // Modal should be visible
    await screen.findByText(/inst-123/);
    expect(screen.getByText("http://test.example.com")).toBeInTheDocument();
  });

  it("closes peer detail modal on close button click", async () => {
    const mockPeers = [
      {
        peer_id: "peer-1",
        name: "Test Peer",
        status: "online",
        region: "us-east-1",
        endpoint: "http://test.example.com",
        instance_id: "inst-123",
        last_seen: "2026-09-24T12:00:00Z",
      },
    ];

    vi.mocked(a2aApi.listDiscoveryPeers).mockResolvedValueOnce({
      peers: mockPeers,
      total: 1,
    });

    render(
      <TestWrapper>
        <DiscoveryPanel />
      </TestWrapper>,
    );

    // Wait for peers to load
    await screen.findByText("Test Peer");

    // Open detail modal
    const detailsButton = screen.getByText("Details");
    fireEvent.click(detailsButton);

    // Wait for modal to appear
    await screen.findByText(/inst-123/);

    // Close modal
    const closeButton = screen.getByLabelText("Close detail view");
    fireEvent.click(closeButton);

    // Modal content should be gone
    expect(screen.queryByText(/inst-123/)).not.toBeInTheDocument();
  });

  it("handles API errors gracefully", async () => {
    vi.mocked(a2aApi.listDiscoveryPeers).mockRejectedValueOnce(
      new Error("Failed to fetch peers"),
    );

    render(
      <TestWrapper>
        <DiscoveryPanel />
      </TestWrapper>,
    );

    // Wait for error message
    await screen.findByText("Failed to Load Peers");
    expect(screen.getByText("Failed to fetch peers")).toBeInTheDocument();

    // Retry button should be visible
    const retryButton = screen.getByText("Retry");
    expect(retryButton).toBeInTheDocument();
  });

  it("has proper ARIA labels for accessibility", async () => {
    const mockPeers = [
      {
        peer_id: "peer-1",
        name: "Test Peer",
        status: "online",
        region: "us-east-1",
        endpoint: null,
        instance_id: "inst-123",
        last_seen: null,
      },
    ];

    vi.mocked(a2aApi.listDiscoveryPeers).mockResolvedValueOnce({
      peers: mockPeers,
      total: 1,
    });

    render(
      <TestWrapper>
        <DiscoveryPanel />
      </TestWrapper>,
    );

    // Wait for peers to load
    await screen.findByText("Test Peer");

    // Check for ARIA labels
    const searchInput = screen.getByLabelText("Search peers");
    expect(searchInput).toBeInTheDocument();

    const refreshButton = screen.getByLabelText("Refresh peer list");
    expect(refreshButton).toBeInTheDocument();
  });

  it("refreshes peer list on refresh button click", async () => {
    const mockPeers = [
      {
        peer_id: "peer-1",
        name: "Test Peer",
        status: "online",
        region: "us-east-1",
        endpoint: null,
        instance_id: "inst-123",
        last_seen: null,
      },
    ];

    vi.mocked(a2aApi.listDiscoveryPeers)
      .mockResolvedValueOnce({
        peers: mockPeers,
        total: 1,
      })
      .mockResolvedValueOnce({
        peers: mockPeers,
        total: 1,
      });

    render(
      <TestWrapper>
        <DiscoveryPanel />
      </TestWrapper>,
    );

    // Wait for initial load
    await screen.findByText("Test Peer");

    // Click refresh
    const refreshButton = screen.getByLabelText("Refresh peer list");
    fireEvent.click(refreshButton);

    // API should be called again
    expect(a2aApi.listDiscoveryPeers).toHaveBeenCalledTimes(2);
  });
});
