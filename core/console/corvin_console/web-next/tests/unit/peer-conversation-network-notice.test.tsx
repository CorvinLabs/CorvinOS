/**
 * Member-only network + a pairing the peer ended (2026-10-21, measured on two fresh installs):
 * the real PeerConversation says WHY nothing can be sent — "member seat required" from the feed's
 * `network` verdict, "pairing ended by <peer>" from the peer's revoke stamp — and disables the
 * composer on the same rule. Before, both cases read as an ordinary "unreachable" peer.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeerConversation } from "@/components/chat/PeerConversation";
import { getA2AFeed, getPeerThreadCommands, type A2AFeedPeer, type A2ANetworkStatus } from "@/lib/api/a2a";
import { listConversations } from "@/lib/api/federation";

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return { ...actual, getA2AFeed: vi.fn(), getPeerThreadCommands: vi.fn(), sendA2AFeedMessage: vi.fn() };
});
vi.mock("@/lib/api/federation", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/federation")>("@/lib/api/federation");
  return { ...actual, listConversations: vi.fn(), stopConversation: vi.fn() };
});

function feed(peer: Partial<A2AFeedPeer>, network?: A2ANetworkStatus) {
  vi.mocked(getA2AFeed).mockResolvedValue({
    peers: [{ peer_id: "peer-1", label: "Test Peer", can_send: true, can_receive: true, enabled: true, ...peer }],
    messages: [], stages: {}, network,
  } as unknown as Awaited<ReturnType<typeof getA2AFeed>>);
}

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><PeerConversation peerId="peer-1" csrf="c" /></QueryClientProvider>);
}

const sendButton = () => screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;

describe("PeerConversation — why nothing can be sent", () => {
  beforeEach(() => {
    vi.mocked(getPeerThreadCommands).mockResolvedValue({ commands: [] });
    vi.mocked(listConversations).mockResolvedValue({ conversations: [] });
  });

  it("a member-locked install shows the member-seat notice and disables the composer", async () => {
    feed({}, { allowed: false, reason: "member_seat_required", detail: "A2A is member-only on this install: pairing, pings and messages need a member seat." });
    renderIt();
    const notice = await screen.findByTestId("peer-network-notice");
    expect(notice.getAttribute("data-reason")).toBe("member_seat_required");
    expect(notice.getAttribute("title")).toContain("member seat");
    expect(sendButton().disabled).toBe(true);
    expect(screen.queryByTestId("peer-revoked-notice")).toBeNull();
  });

  it("a pairing the peer ended says so, named, and disables the composer", async () => {
    feed({ peer_revoked_at: 1_792_590_475 }, { allowed: true, reason: null, detail: null });
    renderIt();
    const notice = await screen.findByTestId("peer-revoked-notice");
    expect(notice.textContent).toContain("pairing ended by Test Peer");
    expect(notice.getAttribute("title")).toContain("import a new token");
    expect(sendButton().disabled).toBe(true);
    expect(screen.queryByTestId("peer-network-notice")).toBeNull();
  });

  it("a peer whose install has no member seat is named, and the composer is disabled", async () => {
    // round 2 (2026-10-21): the PEER's hello answer was "license_required" — before, the thread read
    // as an ordinary unreachable peer and the pairing hint said "set your own A2A URL".
    feed({ peer_network_denied_at: 1_792_600_008 }, { allowed: true, reason: null, detail: null });
    renderIt();
    const notice = await screen.findByTestId("peer-no-seat-notice");
    expect(notice.textContent).toContain("Test Peer has no member seat");
    expect(notice.getAttribute("title")).toContain("no member seat for the A2A network");
    expect(sendButton().disabled).toBe(true);
    expect(screen.queryByTestId("peer-network-notice")).toBeNull();
    expect(screen.queryByTestId("peer-revoked-notice")).toBeNull();
  });

  it("an allowed, live pairing shows neither and keeps the composer usable", async () => {
    feed({}, { allowed: true, reason: null, detail: null });
    renderIt();
    await screen.findByTestId("peer-conversation");
    expect(screen.queryByTestId("peer-network-notice")).toBeNull();
    expect(screen.queryByTestId("peer-revoked-notice")).toBeNull();
    // empty composer: disabled only because there is nothing to send
    expect(sendButton().disabled).toBe(true);
  });

  it("an older host without the verdict is treated as allowed (no false notice)", async () => {
    feed({});
    renderIt();
    await screen.findByTestId("peer-conversation");
    expect(screen.queryByTestId("peer-network-notice")).toBeNull();
  });
});
