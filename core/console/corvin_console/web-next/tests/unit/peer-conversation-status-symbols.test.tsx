/**
 * The status symbol is stuck to EVERY message in the real PeerConversation — in every
 * state, including the quiet ones (ok / sent / received) that used to show nothing —
 * and it follows the stage the peer reports on the next feed poll.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeerConversation } from "@/components/chat/PeerConversation";
import { getA2AFeed, getPeerThreadCommands, type A2AFeedMessage } from "@/lib/api/a2a";
import { listConversations } from "@/lib/api/federation";

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return { ...actual, getA2AFeed: vi.fn(), getPeerThreadCommands: vi.fn() };
});
vi.mock("@/lib/api/federation", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/federation")>("@/lib/api/federation");
  return { ...actual, listConversations: vi.fn(), stopConversation: vi.fn() };
});

const NOW_S = () => Math.floor(Date.now() / 1000);

function feedMsg(p: Partial<A2AFeedMessage>): A2AFeedMessage {
  return {
    id: p.id ?? Math.random().toString(36), ts: p.ts ?? NOW_S() - 10,
    direction: p.direction ?? "out", kind: p.kind ?? "task",
    peer_id: "peer-1", peer_label: "Test Peer", task_id: p.task_id ?? "t1",
    status: p.status ?? "ok", text: p.text ?? "x", data: {}, attachments: [],
    duration_ms: null, error: p.error ?? null, thread_ref: null,
  };
}

function feed(messages: A2AFeedMessage[], stages: Record<string, unknown> = {}) {
  vi.mocked(getA2AFeed).mockResolvedValue({
    peers: [{ peer_id: "peer-1", label: "Test Peer", can_send: true, can_receive: true }],
    messages, stages,
  } as unknown as Awaited<ReturnType<typeof getA2AFeed>>);
}

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><PeerConversation peerId="peer-1" csrf="c" /></QueryClientProvider>);
}

const symbolOf = (row: HTMLElement) => within(row).getByTestId("peer-message-status");

describe("PeerConversation — a status symbol on every message", () => {
  beforeEach(() => {
    vi.mocked(getPeerThreadCommands).mockResolvedValue({ commands: [] });
    vi.mocked(listConversations).mockResolvedValue({ conversations: [] });
  });

  it("every message row carries a symbol, whatever its state", async () => {
    feed([
      feedMsg({ id: "1", task_id: "a", status: "queued" }),
      feedMsg({ id: "2", task_id: "b", status: "sent" }),
      feedMsg({ id: "3", task_id: "c", status: "sent" }),
      feedMsg({ id: "4", task_id: "c", kind: "response", direction: "in", status: "ok" }),
      feedMsg({ id: "5", task_id: "d", direction: "in", status: "received" }),
      feedMsg({ id: "6", task_id: "d", kind: "response", direction: "out", status: "ok" }),
      feedMsg({ id: "7", task_id: "e", status: "error", error: "Unable to reach endpoint" }),
    ]);
    renderIt();
    const rows = await screen.findAllByTestId("peer-message");
    expect(rows).toHaveLength(7);
    for (const r of rows) expect(symbolOf(r)).toBeTruthy();
    const keys = rows.map((r) => symbolOf(r).getAttribute("data-status"));
    expect(keys).toEqual(["queued", "sent", "done", "reply-ok", "in-answered", "reply-ok", "failed"]);
  });

  it("follows the stage the peer reports: working shows the spinner, the chain and its age", async () => {
    feed([feedMsg({ id: "1", task_id: "w", status: "sent" })],
      { w: { stage: "processing", stage_seq: 3, ts: NOW_S() - 7, reason: "" } });
    renderIt();
    const row = (await screen.findAllByTestId("peer-message"))[0];
    const sym = symbolOf(row);
    expect(sym.getAttribute("data-status")).toBe("working");
    expect(within(sym).getByText("Agent working")).toBeTruthy();
    const dots = within(sym).getByTestId("peer-message-chain").children;
    expect(Array.from(dots).map((d) => d.getAttribute("data-state")))
      .toEqual(["done", "done", "done", "done", "current", "pending"]);
    expect(sym.textContent).toMatch(/ago|just now/);
    expect(sym.getAttribute("title")).toMatch(/Agent working/);
  });

  it("an unconfirmed send with a known stage shows that stage, not a dead end", async () => {
    feed([
      feedMsg({ id: "1", task_id: "u", status: "sent" }),
      feedMsg({ id: "2", task_id: "u", kind: "response", direction: "in", status: "unconfirmed",
        error: "Delivery unconfirmed — the peer may have received and run it." }),
    ], { u: { stage: "processing", stage_seq: 2, ts: NOW_S() - 30, reason: "" } });
    renderIt();
    const rows = await screen.findAllByTestId("peer-message");
    expect(symbolOf(rows[0]).getAttribute("data-status")).toBe("working");
    expect(symbolOf(rows[0]).getAttribute("title")).toMatch(/last known state/);
  });

  it("a refusal from the peer is a cross with the reason in the tooltip", async () => {
    feed([feedMsg({ id: "1", task_id: "r", status: "sent" })],
      { r: { stage: "rejected", stage_seq: 2, ts: NOW_S() - 2, reason: "busy" } });
    renderIt();
    const sym = symbolOf((await screen.findAllByTestId("peer-message"))[0]);
    expect(sym.getAttribute("data-status")).toBe("failed");
    expect(sym.getAttribute("title")).toMatch(/busy with other tasks/);
  });

  it("an older host without `stages` still draws every symbol", async () => {
    vi.mocked(getA2AFeed).mockResolvedValue({
      peers: [{ peer_id: "peer-1", label: "Test Peer", can_send: true, can_receive: true }],
      messages: [feedMsg({ id: "1", status: "sent" })],
    } as unknown as Awaited<ReturnType<typeof getA2AFeed>>);
    renderIt();
    const sym = symbolOf((await screen.findAllByTestId("peer-message"))[0]);
    await waitFor(() => expect(sym.getAttribute("data-status")).toBe("sent"));
  });
});
