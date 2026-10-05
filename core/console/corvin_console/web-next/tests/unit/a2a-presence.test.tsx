/**
 * Peer presence in the chat sidebar (2026-10-05). The dot used to be green
 * whenever the peer was allowed to send or receive — a peer unreachable for
 * a week showed as online. The dot now follows the measured `presence`
 * from the backend, and a missing field never reads as online.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import * as React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { presenceView, fmtAgo } from "@/lib/a2a-presence";
import { ChatContextSidebar } from "@/components/chat/ChatContextSidebar";
import { getA2AFeed, listDiscoveryPeers } from "@/lib/api/a2a";

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return { ...actual, getA2AFeed: vi.fn(), listDiscoveryPeers: vi.fn() };
});
vi.mock("@/lib/api/chat-groups", () => ({ listGroups: vi.fn().mockResolvedValue([]), createGroup: vi.fn() }));
vi.mock("@/lib/api/pending-actions", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/pending-actions")>("@/lib/api/pending-actions");
  return { ...actual, listPendingSends: vi.fn().mockResolvedValue([]), listPendingTokenRequests: vi.fn().mockResolvedValue([]) };
});

const NOW = 2_000_000_000;

describe("presenceView", () => {
  it("never reports online without a backend verdict", () => {
    expect(presenceView({}).presence).toBe("unknown");
  });
  it("shows how long ago an offline peer was last seen", () => {
    const v = presenceView({ presence: "offline", last_check_at: NOW - 30, last_ok_at: NOW - 7 * 86400 }, NOW);
    expect(v.label).toBe("offline · seen 7 d ago");
    expect(v.dotClass).not.toContain("emerald");
  });
  it("online is green", () => {
    expect(presenceView({ presence: "online", last_check_at: NOW - 5, last_ok_at: NOW - 5 }, NOW).dotClass).toContain("emerald");
  });
  it("fmtAgo", () => {
    expect(fmtAgo(NOW - 10, NOW)).toBe("just now");
    expect(fmtAgo(NOW - 600, NOW)).toBe("10 min ago");
    expect(fmtAgo(null, NOW)).toBeNull();
  });
});

describe("ChatContextSidebar peer dots", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(listDiscoveryPeers).mockResolvedValue({ peers: [], total: 0 });
    vi.mocked(getA2AFeed).mockResolvedValue({
      tenant_id: "_default", ts: NOW, retention_days: 30, messages: [], has_more: false, last_seq: 0,
      peers: [
        { peer_id: "a", label: "stale", state: "UNREACHABLE", can_send: true, can_receive: true, enabled: true,
          presence: "offline", last_check_at: NOW - 30, last_ok_at: NOW - 650_000 },
        { peer_id: "b", label: "live", state: "ACTIVE", can_send: true, can_receive: true, enabled: true,
          presence: "online", last_check_at: NOW - 5, last_ok_at: NOW - 5 },
      ],
    });
  });

  it("a reachable-by-permission but offline peer is not shown online", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <ChatContextSidebar csrf="x" sessionsPanel={null} sessionsAction={null} initialMode="peers" />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const rows = await screen.findAllByTestId("peer-row");
    const byName = Object.fromEntries(rows.map((r) => [r.textContent?.includes("stale") ? "stale" : "live", r]));
    expect(byName.stale.getAttribute("data-presence")).toBe("offline");
    expect(byName.stale.querySelector("[data-testid=presence-dot]")?.className).not.toContain("emerald");
    expect(byName.stale.textContent).toContain("offline");
    expect(byName.live.getAttribute("data-presence")).toBe("online");
    expect(byName.live.querySelector("[data-testid=presence-dot]")?.className).toContain("emerald");
  });
});
