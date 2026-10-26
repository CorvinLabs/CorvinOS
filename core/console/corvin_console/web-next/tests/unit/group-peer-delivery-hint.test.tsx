/**
 * T-0255 — a group that delivers to a connected instance must say, in the
 * visible UI (not only in a tooltip), that no agent answers there (ADR-2218).
 * A group with no A2A peer delivers nowhere, so it shows no delivery hint.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { GroupConversation } from "@/components/chat/GroupConversation";
import * as chatGroups from "@/lib/api/chat-groups";
import type { ChatGroup, Participant } from "@/lib/api/chat-groups";

vi.mock("@/lib/api/chat-groups", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/chat-groups")>("@/lib/api/chat-groups");
  return {
    ...actual,
    getGroup: vi.fn(),
    listMessages: vi.fn(),
    getGroupCommands: vi.fn(),
  };
});

vi.mock("@/lib/api/a2a", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/a2a")>("@/lib/api/a2a");
  return { ...actual, getA2AFeed: vi.fn().mockResolvedValue({ peers: [], messages: [] }) };
});

const human: Participant = {
  participant_id: "p-human", kind: "human", display_name: "Silvio",
  peer_endpoint_id: null, added_at: 0, added_by: "p-human",
};
const peer: Participant = {
  participant_id: "p-peer", kind: "a2a_peer", display_name: "Gateway peer",
  peer_endpoint_id: "ep-1", added_at: 0, added_by: "p-human",
};

function group(participants: Participant[]): ChatGroup {
  return { group_id: "g-1", title: "Team", created_at: 0, created_by: "p-human", participants };
}

function renderGroup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <GroupConversation groupId="g-1" csrf="csrf-token" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("group delivery hint (T-0255)", () => {
  beforeEach(() => {
    vi.mocked(chatGroups.listMessages).mockResolvedValue([]);
    vi.mocked(chatGroups.getGroupCommands).mockResolvedValue({ commands: [] } as never);
  });

  it("states in visible text that no agent answers when a connected instance receives the message", async () => {
    vi.mocked(chatGroups.getGroup).mockResolvedValue(group([human, peer]));
    renderGroup();

    const hint = await screen.findByTestId("group-peer-delivery-hint");
    expect(hint.textContent).toContain("Delivered to 1 connected instance");
    expect(hint.textContent).toContain("no agent answers");
  });

  it("shows no delivery hint in a group with no A2A peer", async () => {
    vi.mocked(chatGroups.getGroup).mockResolvedValue(group([human]));
    renderGroup();

    await screen.findByText("Team");
    expect(screen.queryByTestId("group-peer-delivery-hint")).toBeNull();
  });
});
