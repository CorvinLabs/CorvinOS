/**
 * E2E-wiring-proof for two things together: the palette's insert-mode switch
 * (Enter inserts with the argument placeholder selected, never sends), and
 * the ADR-2235 Phase 2 routing fix — a `/` line, once actually sent, goes
 * through `sendPeerThreadCommand` (the server-side dispatcher), never
 * `sendA2AFeedMessage` (which would put it on the wire to the peer as plain
 * text — the exact bug reported: `/ask @mine …` reaching the PEER's agent
 * because nothing intercepted it client-side).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeerConversation } from "@/components/chat/PeerConversation";
import { SLASH_COMMANDS } from "@/components/chat/SlashCommandPalette";
import { getA2AFeed, getPeerThreadCommands, sendA2AFeedMessage, sendPeerThreadCommand } from "@/lib/api/a2a";

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return {
    ...actual,
    getA2AFeed: vi.fn(),
    sendA2AFeedMessage: vi.fn(),
    getPeerThreadCommands: vi.fn(),
    sendPeerThreadCommand: vi.fn(),
  };
});

function renderPeerConversation() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PeerConversation peerId="peer-1" csrf="csrf-token" />
    </QueryClientProvider>,
  );
}

describe("PeerConversation slash-command palette", () => {
  beforeEach(() => {
    vi.mocked(getA2AFeed).mockResolvedValue({
      peers: [{ peer_id: "peer-1", label: "Test Peer", can_send: true, can_receive: true }],
      messages: [],
    } as unknown as ReturnType<typeof getA2AFeed> extends Promise<infer T> ? T : never);
    vi.mocked(sendA2AFeedMessage).mockResolvedValue({ accepted: true, peer_id: "peer-1" });
    vi.mocked(getPeerThreadCommands).mockResolvedValue({
      commands: [
        { cmd: "/ask @mine", args: "<task>", desc: "Ask your own agent" },
        { cmd: "/ask @peer", args: "<task>", desc: "Ask the peer's agent" },
        { cmd: "/talk", args: "<topic>", desc: "Start an agent-to-agent conversation" },
      ],
    });
  });

  it("Enter inserts the fetched peer-thread command with its argument placeholder selected, instead of sending", async () => {
    renderPeerConversation();
    await screen.findByText("Test Peer");

    const textarea = screen.getByLabelText("Message to agent") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "/ask" } });

    // Proves the commands came from GET /peer-thread/commands (extraCommands
    // wiring), not a client-side constant.
    await screen.findByText("/ask @mine");
    screen.getByText("/ask @peer");

    fireEvent.keyDown(textarea, { key: "Enter" });

    await waitFor(() => expect(textarea.value).toBe("/ask @mine <task>"));
    await waitFor(() => {
      expect(textarea.selectionStart).toBe("/ask @mine ".length);
      expect(textarea.selectionEnd).toBe("/ask @mine <task>".length);
    });
    expect(sendA2AFeedMessage).not.toHaveBeenCalled();
    expect(sendPeerThreadCommand).not.toHaveBeenCalled();
  });

  it("sends a completed /ask line through sendPeerThreadCommand, never sendA2AFeedMessage", async () => {
    vi.mocked(sendPeerThreadCommand).mockResolvedValue({
      executed: true, kind: "ask_mine", status: "completed", agent_id: "opus-private",
      text: "I am your local agent.", conversation_id: "abc123", task_id: "t1", duration_ms: 42,
    });
    renderPeerConversation();
    await screen.findByText("Test Peer");

    const textarea = screen.getByLabelText("Message to agent") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "/ask @mine welcher agent bist du" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    await waitFor(() => expect(sendPeerThreadCommand).toHaveBeenCalled());
    expect(sendPeerThreadCommand).toHaveBeenCalledWith(
      "peer-1", "/ask @mine welcher agent bist du", "csrf-token",
    );
    expect(sendA2AFeedMessage).not.toHaveBeenCalled();
    await screen.findByText("I am your local agent.");
  });

  it("shows a refusal inline and still never calls sendA2AFeedMessage", async () => {
    vi.mocked(sendPeerThreadCommand).mockResolvedValue({
      executed: false, reason: "unknown command — not sent",
    });
    renderPeerConversation();
    await screen.findByText("Test Peer");

    const textarea = screen.getByLabelText("Message to agent") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "/frobnicate" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    await waitFor(() => expect(sendPeerThreadCommand).toHaveBeenCalled());
    await screen.findByText("unknown command — not sent");
    expect(sendA2AFeedMessage).not.toHaveBeenCalled();
  });

  it("does not offer peer-chat commands to the console session composer's command set", () => {
    // Regression guard for the scoping decision: /ask and /talk are
    // server-fetched peer-thread-only commands, never merged into the
    // shared SLASH_COMMANDS list ChatPane and GroupConversation use.
    expect(SLASH_COMMANDS.some((c) => c.cmd.startsWith("/ask"))).toBe(false);
    expect(SLASH_COMMANDS.some((c) => c.cmd.startsWith("/talk"))).toBe(false);
  });
});
