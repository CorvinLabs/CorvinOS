/**
 * ADR-2235 Phase 3 (console UI) — proves the real PeerConversation component
 * renders all four actors distinctly, labels the Observer-mode empty reply
 * instead of showing a blank bubble (PLAN-0937 "Resolved" note), and shows
 * the inline conversation status banner with a working Stop button.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeerConversation } from "@/components/chat/PeerConversation";
import { getA2AFeed, getPeerThreadCommands, type A2AFeedMessage } from "@/lib/api/a2a";
import { listConversations, stopConversation, type ConversationSummary } from "@/lib/api/federation";

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return { ...actual, getA2AFeed: vi.fn(), getPeerThreadCommands: vi.fn() };
});
vi.mock("@/lib/api/federation", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/federation")>("@/lib/api/federation");
  return { ...actual, listConversations: vi.fn(), stopConversation: vi.fn() };
});

function feedMsg(p: Partial<A2AFeedMessage>): A2AFeedMessage {
  return {
    id: p.id ?? Math.random().toString(36), seq: p.seq, ts: p.ts ?? 1,
    direction: p.direction ?? "out", kind: p.kind ?? "task",
    peer_id: "peer-1", peer_label: "Test Peer", task_id: p.task_id ?? "t1",
    status: p.status ?? "ok", text: p.text ?? "", data: p.data ?? {},
    attachments: p.attachments ?? [], duration_ms: null, error: p.error ?? null,
    thread_ref: p.thread_ref ?? null,
  };
}

function conv(p: Partial<ConversationSummary>): ConversationSummary {
  return {
    conversation_id: p.conversation_id ?? "conv-1", status: p.status ?? "running",
    reason: null, local: null, peer: p.peer ?? { agent_id: "opus", model: "x", address: "a", endpoint_id: "peer-1" },
    max_turns: 6, started_at: 1, ended_at: null, turns: p.turns ?? 1, topic: p.topic ?? "Agree on a word",
  };
}

function renderPeerConversation() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PeerConversation peerId="peer-1" csrf="csrf-token" />
    </QueryClientProvider>,
  );
}

describe("PeerConversation — four-actor roles and conversation banner", () => {
  beforeEach(() => {
    vi.mocked(getPeerThreadCommands).mockResolvedValue({ commands: [] });
    vi.mocked(listConversations).mockResolvedValue({ conversations: [] });
    vi.mocked(stopConversation).mockResolvedValue({ stopping: true });
  });

  it("labels all four actors distinctly, overriding to local_agent via thread_ref", async () => {
    vi.mocked(getA2AFeed).mockResolvedValue({
      peers: [{ peer_id: "peer-1", label: "Test Peer", can_send: true, can_receive: true }],
      messages: [
        feedMsg({ id: "1", task_id: "t1", direction: "out", kind: "task", text: "hi there" }),
        feedMsg({ id: "2", task_id: "t1", direction: "in", kind: "response", text: "hello" }),
        feedMsg({ id: "3", task_id: "t2", direction: "in", kind: "task", text: "a question for you" }),
        feedMsg({ id: "4", task_id: "t2", direction: "out", kind: "response", text: "an answer" }),
        feedMsg({
          id: "5", task_id: "t3", direction: "out", kind: "task", text: "<framing+history prompt>",
          thread_ref: { kind: "conversation", id: "conv-1", author_role: "local_agent", agent_id: "opus" },
        }),
      ],
    } as unknown as ReturnType<typeof getA2AFeed> extends Promise<infer T> ? T : never);

    renderPeerConversation();
    const rows = await screen.findAllByTestId("peer-message");
    expect(rows).toHaveLength(5);
    const roleOf = (i: number) => within(rows[i]).getByTestId("peer-message-role").textContent;

    expect(roleOf(0)).toBe("You");                 // out+task, no thread_ref
    expect(roleOf(1)).toBe("Test Peer's agent");    // in+response
    expect(roleOf(2)).toBe("Test Peer");            // in+task
    expect(roleOf(3)).toBe("Your agent");            // out+response
    expect(roleOf(4)).toBe("Your agent");            // out+task WITH thread_ref override — NOT "You"
  });

  it("labels an Observer-mode empty reply instead of showing a blank bubble", async () => {
    vi.mocked(getA2AFeed).mockResolvedValue({
      peers: [{ peer_id: "peer-1", label: "Test Peer", can_send: true, can_receive: true }],
      messages: [feedMsg({ direction: "in", kind: "response", status: "ok", data: {}, text: "" })],
    } as unknown as ReturnType<typeof getA2AFeed> extends Promise<infer T> ? T : never);

    renderPeerConversation();
    await screen.findByText("no agent — Test Peer has not granted Executor permission");
  });

  it("shows the running conversation inline and Stop ends it; it collapses once not running", async () => {
    vi.mocked(getA2AFeed).mockResolvedValue({
      peers: [{ peer_id: "peer-1", label: "Test Peer", can_send: true, can_receive: true }],
      messages: [],
    } as unknown as ReturnType<typeof getA2AFeed> extends Promise<infer T> ? T : never);
    vi.mocked(listConversations).mockResolvedValue({
      conversations: [conv({ status: "running", turns: 2, topic: "Agree on a word" })],
    });

    renderPeerConversation();
    const banner = await screen.findByTestId("active-conversation-banner");
    expect(within(banner).getByText("Agree on a word")).toBeTruthy();
    expect(within(banner).getByText("2 turns")).toBeTruthy();

    vi.mocked(listConversations).mockResolvedValue({
      conversations: [conv({ status: "completed", turns: 2, topic: "Agree on a word" })],
    });
    fireEvent.click(within(banner).getByRole("button", { name: /stop/i }));
    await waitFor(() => expect(stopConversation).toHaveBeenCalledWith("conv-1", "csrf-token"));
    await waitFor(() => expect(screen.queryByTestId("active-conversation-banner")).toBeNull());
  });
});
