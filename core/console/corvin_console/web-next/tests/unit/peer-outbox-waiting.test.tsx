/**
 * T-0252 — a message kept in the sender outbox shows "Waiting for peer" and can be withdrawn.
 *
 * Renders the real PeerConversation. Only the network functions are mocked, so the path under test is
 * the one a user takes: the feed reports the waiting message, the row says so, Cancel calls the route
 * and the feed is read again.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeerConversation } from "@/components/chat/PeerConversation";
import { cancelWaitingA2AMessage, getA2AFeed, getPeerThreadCommands } from "@/lib/api/a2a";

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return {
    ...actual,
    getA2AFeed: vi.fn(),
    getPeerThreadCommands: vi.fn(),
    cancelWaitingA2AMessage: vi.fn(),
  };
});

type Feed = Awaited<ReturnType<typeof getA2AFeed>>;

function feed(outbox: Record<string, { deadline_at: number; attempts: number }>): Feed {
  const now = Math.floor(Date.now() / 1000);
  return {
    tenant_id: "_default", ts: now, retention_days: 30, has_more: false, last_seq: 1,
    peers: [{ peer_id: "peer-1", label: "Test Peer", can_send: true, can_receive: true }],
    network: { allowed: true, reason: "", detail: null },
    stages: {},
    outbox,
    messages: [{
      id: "m1", seq: 1, ts: now - 30, direction: "out", kind: "task", peer_id: "peer-1",
      peer_label: "Test Peer", task_id: "t1", status: "queued", text: "please read this later",
      data: {}, attachments: [], duration_ms: null, error: null, thread_ref: null,
    }],
  } as unknown as Feed;
}

function renderConversation() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PeerConversation peerId="peer-1" csrf="csrf-token" />
    </QueryClientProvider>,
  );
}

describe("PeerConversation outbox row", () => {
  beforeEach(() => {
    vi.mocked(getPeerThreadCommands).mockResolvedValue({ commands: [] });
    vi.mocked(cancelWaitingA2AMessage).mockResolvedValue({ cancelled: true, task_id: "t1" });
  });

  it("a waiting message says so and offers Cancel, which calls the route and re-reads the feed", async () => {
    const now = Math.floor(Date.now() / 1000);
    vi.mocked(getA2AFeed).mockResolvedValue(feed({ t1: { deadline_at: now + 7200, attempts: 1 } }));
    renderConversation();

    const status = await screen.findByTestId("peer-message-status");
    expect(status.getAttribute("data-status")).toBe("waiting");
    expect(status.textContent).toContain("Waiting for peer");

    const calls = vi.mocked(getA2AFeed).mock.calls.length;
    fireEvent.click(screen.getByTestId("peer-message-cancel"));
    await waitFor(() => expect(cancelWaitingA2AMessage).toHaveBeenCalledWith("t1", "csrf-token"));
    await waitFor(() => expect(vi.mocked(getA2AFeed).mock.calls.length).toBeGreaterThan(calls));
  });

  it("a message that is not in the outbox has no Cancel button", async () => {
    vi.mocked(getA2AFeed).mockResolvedValue(feed({}));
    renderConversation();
    const status = await screen.findByTestId("peer-message-status");
    expect(status.getAttribute("data-status")).toBe("queued");
    expect(screen.queryByTestId("peer-message-cancel")).toBeNull();
  });
});
