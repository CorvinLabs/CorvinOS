/**
 * Regression test (2026-10-05 console-navigation refactor, Phase 3 /
 * E2E-wiring-proof): PeerConversation's composer let an operator attach a
 * file (AttachmentChip appeared, upload "succeeded"), but handleSend always
 * sent `attachments: []` — the file was silently dropped on send, with no
 * error shown. The fix wires the existing encodeFilesForA2A helper (already
 * used by agent-hub.tsx's AgentLiveFeed) into PeerConversation's
 * useAttachmentUpload call. This proves the real send path carries the
 * file's content, not just that encodeFilesForA2A works in isolation.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import * as React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeerConversation } from "@/components/chat/PeerConversation";
import { getA2AFeed, sendA2AFeedMessage } from "@/lib/api/a2a";

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return {
    ...actual,
    getA2AFeed: vi.fn(),
    sendA2AFeedMessage: vi.fn(),
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

describe("PeerConversation attachments", () => {
  beforeEach(() => {
    vi.mocked(getA2AFeed).mockResolvedValue({
      peers: [{ peer_id: "peer-1", label: "Test Peer", can_send: true, can_receive: true }],
      messages: [],
    } as unknown as ReturnType<typeof getA2AFeed> extends Promise<infer T> ? T : never);
    vi.mocked(sendA2AFeedMessage).mockResolvedValue({ accepted: true, peer_id: "peer-1" });
  });

  it("sends the file's real content instead of dropping it silently", async () => {
    renderPeerConversation();
    await screen.findByText("Test Peer");

    const file = new File(["hello world"], "note.txt", { type: "text/plain" });
    const input = screen.getByTestId("file-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await screen.findByTestId("attachment-chip");

    const sendButton = screen.getByRole("button", { name: "Send" });
    fireEvent.click(sendButton);

    await waitFor(() => expect(sendA2AFeedMessage).toHaveBeenCalled());
    const [body] = vi.mocked(sendA2AFeedMessage).mock.calls[0];
    expect(body.attachments).toHaveLength(1);
    expect(body.attachments[0].name).toBe("note.txt");
    expect(body.attachments[0].content_b64).toBeTruthy();
    expect(body.attachments[0].content_b64).not.toBe("");
  });

  it("allows sending with an attachment and no text", async () => {
    renderPeerConversation();
    await screen.findByText("Test Peer");

    const file = new File(["data"], "image.png", { type: "image/png" });
    const input = screen.getByTestId("file-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    await screen.findByTestId("attachment-chip");

    const sendButton = screen.getByRole("button", { name: "Send" });
    expect(sendButton).not.toBeDisabled();
    fireEvent.click(sendButton);

    await waitFor(() => expect(sendA2AFeedMessage).toHaveBeenCalled());
  });
});
