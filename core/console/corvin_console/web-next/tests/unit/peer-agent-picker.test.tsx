/**
 * Peer picker of the `peer_agent` workflow node: pure choice logic, the node's required fields,
 * and the component against the federation API (mocked at the fetch boundary of lib/api/federation).
 * An empty federation reads as an empty state, never as example data.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import * as React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeerAgentPicker } from "@/components/workflows/PeerAgentPicker";
import { listFederationPeers, listPeerAgents, refreshPeerCatalog } from "@/lib/api/federation";
import type { FederationPeer, PeerAgent } from "@/lib/api/federation";
import {
  choiceValue,
  currentIsOffered,
  firstChoice,
  peerChoices,
  splitChoice,
  stalePeers,
} from "@/lib/peer-agent-choices";
import { NODE_PALETTE, fieldsFor, missingRequiredFields } from "@/lib/workflow-node-types";

vi.mock("@/lib/api/federation", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/federation")>("@/lib/api/federation");
  return { ...actual, listFederationPeers: vi.fn(), listPeerAgents: vi.fn(), refreshPeerCatalog: vi.fn() };
});

const agent = (endpoint: string, id: string, model = "m"): PeerAgent => ({
  agent_id: id, address: `agent://i/${id}`, endpoint_id: endpoint, peer_instance_id: "i", model, capabilities: ["analysis"],
});
const peer = (endpoint: string, fresh: boolean): FederationPeer => ({
  endpoint_id: endpoint, peer_instance_id: "i", agent_count: 1, fresh,
});

describe("peer choices", () => {
  it("lists agents in a stable order and round-trips the option value", () => {
    const c = peerChoices([agent("ep_b", "z"), agent("ep_a", "y"), agent("ep_a", "x")]);
    expect(c.map((x) => x.value)).toEqual([choiceValue("ep_a", "x"), choiceValue("ep_a", "y"), choiceValue("ep_b", "z")]);
    expect(splitChoice(c[1].value)).toEqual({ endpoint_id: "ep_a", agent_id: "y" });
    expect(splitChoice("nonsense")).toBeNull();
    expect(firstChoice(c)?.agent_id).toBe("x");
  });
  it("nothing offered means nothing chosen", () => {
    expect(peerChoices([])).toEqual([]);
    expect(firstChoice([])).toBeNull();
  });
  it("flags stale catalogs and targets that are no longer offered", () => {
    expect(stalePeers([peer("a", true), peer("b", false)]).map((p) => p.endpoint_id)).toEqual(["b"]);
    const c = peerChoices([agent("ep_a", "x")]);
    expect(currentIsOffered(c, "ep_a", "x")).toBe(true);
    expect(currentIsOffered(c, "ep_a", "gone")).toBe(false);
    expect(currentIsOffered(c, "", "")).toBe(true); // nothing chosen yet: nothing to warn about
  });
});

describe("peer_agent node definition", () => {
  it("requires peer, agent and instruction; the picker owns the first two", () => {
    expect(missingRequiredFields({ type: "peer_agent", id: "p" })).toEqual(["peer", "peer agent", "instruction"]);
    expect(fieldsFor({ type: "peer_agent" }).filter((f) => f.kind === "peer").map((f) => f.path)).toEqual([
      "endpoint_id", "agent_id",
    ]);
  });
  it("the palette builds a complete node from a catalog target and an incomplete one without", () => {
    const entry = NODE_PALETTE.find((p) => p.type === "peer_agent")!;
    expect(entry.needsPeer).toBe(true);
    expect(missingRequiredFields(entry.create("p1", { endpoint_id: "ep_a", agent_id: "x" }))).toEqual([]);
    expect(missingRequiredFields(entry.create("p1"))).toEqual(["peer", "peer agent"]);
  });
});

function mount(props: Partial<React.ComponentProps<typeof PeerAgentPicker>> = {}) {
  const onChange = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PeerAgentPicker endpointId="" agentId="" csrf="t" onChange={onChange} {...props} />
    </QueryClientProvider>,
  );
  return onChange;
}

describe("PeerAgentPicker", () => {
  beforeEach(() => {
    vi.mocked(listFederationPeers).mockReset();
    vi.mocked(listPeerAgents).mockReset();
    vi.mocked(refreshPeerCatalog).mockReset();
  });

  it("shows an empty state when no peer is paired", async () => {
    vi.mocked(listFederationPeers).mockResolvedValue({ peers: [] });
    vi.mocked(listPeerAgents).mockResolvedValue({ agents: [] });
    mount();
    expect(await screen.findByTestId("peer-agent-empty")).toBeTruthy();
    expect(screen.queryByTestId("peer-agent-select")).toBeNull();
  });

  it("offers the agents of fresh catalogs and reports the picked pair", async () => {
    vi.mocked(listFederationPeers).mockResolvedValue({ peers: [peer("ep_a", true)] });
    vi.mocked(listPeerAgents).mockResolvedValue({ agents: [agent("ep_a", "reviewer", "opus")] });
    const onChange = mount();
    const select = (await screen.findByTestId("peer-agent-select")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: choiceValue("ep_a", "reviewer") } });
    expect(onChange).toHaveBeenCalledWith("ep_a", "reviewer");
  });

  it("marks a stale catalog and refreshes it on request", async () => {
    vi.mocked(listFederationPeers).mockResolvedValue({ peers: [peer("ep_old", false)] });
    vi.mocked(listPeerAgents).mockResolvedValue({ agents: [] });
    vi.mocked(refreshPeerCatalog).mockResolvedValue({ agents: [] });
    mount();
    const row = await screen.findByTestId("peer-stale-ep_old");
    expect(row.textContent).toContain("stale");
    fireEvent.click(row.querySelector("button")!);
    await waitFor(() => expect(refreshPeerCatalog).toHaveBeenCalledWith("ep_old", "t"));
  });

  it("warns when the saved target is not offered any more but keeps it selectable", async () => {
    vi.mocked(listFederationPeers).mockResolvedValue({ peers: [peer("ep_a", true)] });
    vi.mocked(listPeerAgents).mockResolvedValue({ agents: [agent("ep_a", "other")] });
    mount({ endpointId: "ep_a", agentId: "reviewer" });
    expect(await screen.findByTestId("peer-agent-not-offered")).toBeTruthy();
    expect((screen.getByTestId("peer-agent-select") as HTMLSelectElement).value).toBe(choiceValue("ep_a", "reviewer"));
  });
});
